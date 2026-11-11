"""단일 사용자 시절 DB → 다중 사용자 스키마 마이그레이션."""
import os
import sqlite3

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from app.db import Base
from scripts import migrate


def make_old_db(path, with_data=True):
    """예전(0001) 구조 DB를 만들고, 기존에 쓰던 것처럼 데이터를 넣는다."""
    url = f"sqlite:///{path}"
    cfg = migrate.alembic_config(url)
    command.upgrade(cfg, "0001")
    con = sqlite3.connect(path)
    con.execute("DELETE FROM alembic_version")  # 실제 운영 DB처럼 버전 기록이 없는 상태
    con.execute("DROP TABLE alembic_version")
    con.execute("INSERT INTO settings VALUES (1, 'old@example.com', 7)")
    for code, m in [("DAY", 60), ("EVENING", 120), ("NIGHT", 60), ("OFF", 180)]:
        con.execute("INSERT INTO shift_types VALUES (?, ?, ?, '07:00:00.000000')", (code, code.lower(), m))
    con.execute("INSERT INTO sentences VALUES (1, 'Hello', '안녕', 0)")
    if with_data:
        con.execute("INSERT INTO calendar_days VALUES ('2026-11-02', 'DAY', 0)")
        con.execute("INSERT INTO tasks VALUES (5, 'Reading', 'R', 60, 3, 1)")
        con.execute("INSERT INTO weeks VALUES (1, '2026-11-02', '2026-11-01 10:00:00.000000', '')")
        con.execute(
            "INSERT INTO assignments VALUES (10, '2026-11-02', '2026-11-02', 5, 'regular', 'missed', NULL, 0, NULL)"
        )
        con.execute(
            "INSERT INTO assignments VALUES (11, '2026-11-02', '2026-11-08', 5, 'makeup', 'scheduled', 10, 0, NULL)"
        )
        con.execute("INSERT INTO action_tokens VALUES ('tok', 11, 'done', '2026-12-01 00:00:00.000000', NULL)")
        con.execute(
            "INSERT INTO mail_logs VALUES (1, '2026-11-02', '2026-11-02 06:00:00.000000', 'sent', 1, '')"
        )
    con.commit()
    con.close()
    return url


def test_old_db_is_migrated_to_admin_with_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "Me@Example.com")
    path = tmp_path / "app.db"
    url = make_old_db(path)

    result = migrate.run(url)
    assert result.startswith("upgraded 0001 -> ")  # 최신 버전까지

    backups = os.listdir(tmp_path / "backups")
    assert len(backups) == 1
    old = sqlite3.connect(tmp_path / "backups" / backups[0])
    assert old.execute("select count(*) from settings").fetchone()[0] == 1  # 백업은 예전 구조 그대로
    old.close()

    con = sqlite3.connect(path)
    uid, email, step, admin, cursor, notify, channel = con.execute(
        "select id, email, onboarding_step, is_admin, sentence_cursor, notify_email, notify_channel from users"
    ).fetchone()
    assert (email, step, admin, cursor, notify, channel) == ("me@example.com", 3, 1, 7, "old@example.com", "both")
    for table in ["shift_types", "calendar_days", "tasks", "weeks", "assignments", "sentences", "notify_logs"]:
        owners = {r[0] for r in con.execute(f"select user_id from {table}")}
        assert owners == {uid}, table
    # id와 보충 연결, 메일 토큰이 유지됨
    assert con.execute("select makeup_of_id from assignments where id = 11").fetchone()[0] == 10
    assert con.execute("select assignment_id from action_tokens where token='tok'").fetchone()[0] == 11
    assert con.execute("select channel from notify_logs").fetchone()[0] == "email"
    assert {r[0] for r in con.execute("select name from sqlite_master where type='table'")} >= {
        "users",
        "notify_logs",
        "login_requests",
    }
    assert not [r for r in con.execute("select name from sqlite_master where name like '_old_%'")]
    con.close()

    assert migrate.run(url) == "up to date"  # 두 번 실행해도 안전


def test_existing_data_without_admin_email_fails_and_keeps_db(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    path = tmp_path / "app.db"
    url = make_old_db(path)
    with pytest.raises(RuntimeError, match="ADMIN_EMAIL"):
        migrate.run(url)
    con = sqlite3.connect(path)  # 트랜잭션이 되돌려져 예전 구조가 그대로 남아 있어야 함
    assert con.execute("select count(*) from tasks").fetchone()[0] == 1
    con.close()


def test_old_db_without_user_data_needs_no_admin(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    path = tmp_path / "app.db"
    url = make_old_db(path, with_data=False)
    migrate.run(url)
    con = sqlite3.connect(path)
    assert con.execute("select count(*) from users").fetchone()[0] == 0
    con.close()


def test_fresh_db_schema_matches_models(tmp_path):
    """마이그레이션으로 만든 스키마와 모델 정의가 일치하는지 (Postgres 이전 대비)."""
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    assert migrate.run(url).startswith("upgraded")
    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def test_failure_midway_rolls_back_everything(tmp_path, monkeypatch):
    """마이그레이션 도중 오류가 나도 DB가 반쯤 바뀐 채 남지 않는다 (트랜잭션 DDL)."""
    monkeypatch.setenv("ADMIN_EMAIL", "me@example.com")
    path = tmp_path / "app.db"
    url = make_old_db(path)
    con = sqlite3.connect(path)
    con.execute("DROP TABLE mail_logs")  # 일부러 깨뜨려서 rename 단계 중간에 실패하게 함
    con.commit()
    con.close()
    with pytest.raises(Exception):
        migrate.run(url)
    con = sqlite3.connect(path)
    names = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert "tasks" in names and "users" not in names and not any(n.startswith("_old_") for n in names)
    assert con.execute("select count(*) from tasks").fetchone()[0] == 1
    con.close()
