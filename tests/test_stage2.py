"""2단계: 푸시 전용 전환(0007) · 알림 상태 배너 · 관리자 화면 · healthz · 매일 백업/정리 · 로그 · 리허설."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.config import env_presence_line, settings
from app.main import app
from app.models import CalendarDay, DailyCounter, LoginRequest, NotifyLog, ShiftType, Task
from app.services import maintenance
from app.services.backup import backup_dir, list_backups
from app.services.counters import get as counter
from app.services.daily import notify_due, run_due
from app.services.users import get_or_create_user
from scripts import migrate
from tests.conftest import add_sub, login

ROOT = Path(__file__).resolve().parent.parent
MON = date(2026, 11, 2)


def member(db, email="other@example.com"):
    u = get_or_create_user(db, email)
    u.onboarding_step = 3
    db.commit()
    return u


def setup_day(db, u, send=time(6, 0)):
    db.scalar(select(ShiftType).where(ShiftType.user_id == u.id, ShiftType.code == "DAY")).send_time = send
    db.add(CalendarDay(user_id=u.id, date=MON, shift_code="DAY"))
    db.add(Task(user_id=u.id, name="단어", subject="V", duration_min=30, weekly_count=5))
    db.commit()


# ------------------------------------------------------------------ 0007 푸시 전용 전환
def test_migration_0007_switches_everyone_to_push(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    path = tmp_path / "app.db"
    url = f"sqlite:///{path}"
    command.upgrade(migrate.alembic_config(url), "0006")
    con = sqlite3.connect(path)
    for uid, ch, mail in [(1, "email", "a@example.com"), (2, "both", ""), (3, "push", "")]:
        con.execute(
            "INSERT INTO users (id, email, google_sub, name, timezone, notify_channel, notify_email, sentence_cursor, "
            "onboarding_step, last_prepared_date, is_admin, created_at, last_login_at) "
            "VALUES (?, ?, NULL, '', 'Asia/Seoul', ?, ?, 0, 3, NULL, 0, '2026-11-01 00:00:00.000000', NULL)",
            (uid, f"u{uid}@example.com", ch, mail),
        )
    con.commit()
    con.close()
    assert migrate.run(url).startswith("upgraded 0006 -> ")
    con = sqlite3.connect(path)
    assert con.execute("select notify_channel, notify_email from users order by id").fetchall() == [("push", "")] * 3
    assert con.execute("select count(*) from daily_counters").fetchone()[0] == 0
    assert con.execute("select count(*) from users").fetchone()[0] == 3  # 사용자는 그대로
    con.close()


def test_old_channel_values_still_get_push_only(db, user, pusher):
    setup_day(db, user)
    user.notify_channel, user.notify_email = "email", "x@example.com"  # 0007 이전 값이 남아 있어도
    db.commit()
    add_sub(db, user, "https://web.push.apple.com/me")
    out = notify_due(db, user, datetime.combine(MON, time(6)))
    assert [o.split(":")[0] for o in out] == ["push"] and len(pusher["calls"]) == 1
    assert {log.channel for log in db.scalars(select(NotifyLog))} == {"push"}


# ------------------------------------------------------------------ 알림 상태 배너
def test_push_status_endpoint(db, user):
    other = member(db)
    add_sub(db, user, "https://web.push.apple.com/mine")
    add_sub(db, other, "https://web.push.apple.com/theirs")
    with TestClient(app) as c:
        login(c)
        assert c.post("/push/status", json={"endpoint": "https://web.push.apple.com/mine"}).json() == {"known": True}
        assert c.post("/push/status", json={"endpoint": "https://web.push.apple.com/theirs"}).json() == {"known": False}
        assert c.post("/push/status", json={"endpoint": "https://web.push.apple.com/gone"}).json() == {"known": False}
        assert c.post("/push/status", data={"endpoint": "x"}).status_code in (415, 422)  # JSON 만


def test_banner_markup_on_app_pages_only(db, user):
    with TestClient(app) as c:
        assert 'id="push-alert"' not in c.get("/login").text
        login(c)
        for path in ("/today", "/", "/calendar"):
            html = c.get(path).text
            assert html.count('id="push-alert"') == 1 and "push-status.js" in html, path
            assert 'id="push-alert" class="push-alert" role="status" hidden' in html  # 상태 확인 전에는 숨김
        assert 'id="push-alert"' not in c.get("/settings").text  # 설정에는 알림 상자가 따로 있음


def test_banner_decision_in_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    r = subprocess.run([node, str(ROOT / "tests/js/push-status.test.js")], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr


def test_permission_only_requested_from_button_tap():
    src = (ROOT / "app/static/push-status.js").read_text(encoding="utf-8")
    # requestPermission 은 enable()(버튼 탭 처리) 안에서만 부른다
    assert src.count("requestPermission") == 1
    enable = src.split("function enable()")[1].split("\n  }\n")[0]
    assert "requestPermission" in enable
    assert 'enableBtn.addEventListener("click", enable)' in src


# ------------------------------------------------------------------ 관리자 화면
def test_admin_page_is_404_for_others(db, user):
    member(db)
    with TestClient(app) as c:
        assert c.get("/admin", follow_redirects=False).status_code == 303  # 로그인 전: 로그인으로
        login(c, "other@example.com")
        assert c.get("/admin").status_code == 404
        assert c.get("/admin/backup").status_code == 404
        assert "/admin" not in c.get("/settings").text


def test_admin_page_numbers(db, user, pusher):
    a, b = member(db, "a@example.com"), member(db, "b@example.com")
    for u, ep in ((user, "me"), (a, "a"), (a, "a2")):
        add_sub(db, u, f"https://web.push.apple.com/{ep}")
    add_sub(db, b, "https://web.push.apple.com/b-gone")
    pusher["fail"]["https://web.push.apple.com/b-gone"] = 410
    for u in (user, a, b):
        setup_day(db, u)
    today = clock.today()
    run_due(db, datetime.combine(MON, time(6)))  # user·a 성공, b 는 만료 삭제 → 건너뜀
    for log in db.scalars(select(NotifyLog)):
        log.date = today  # 관리자 화면은 '오늘' 기준
    db.commit()
    with TestClient(app) as c:
        login(c)
        html = c.get("/admin").text
    assert counter(db, "push_removed") == 1  # 오늘(서버 날짜) 집계
    assert "<td>정기 알림</td><td>2</td><td>0</td><td>0</td><td>1</td>" in html
    assert "3대" in html  # 남은 기기
    assert "2 / 3명 (67%)" in html  # 알림을 켠 가입자 / 전체
    assert "2 / 50명" in html  # 가입자(관리자 제외) / 정원
    for leak in ("@example.com", "a@", "b@"):
        assert leak not in html


def test_push_removed_counter_on_410(db, user, pusher):
    add_sub(db, user, "https://web.push.apple.com/old")
    pusher["fail"]["https://web.push.apple.com/old"] = 410
    with TestClient(app) as c:
        login(c)
        c.post("/push/test")
    assert counter(db, "push_removed") == 1


# ------------------------------------------------------------------ healthz
def test_healthz_checks_db(monkeypatch):
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"ok": True, "db": "ok"}
        import app.auth as auth

        class Broken:
            def __enter__(self):
                raise RuntimeError("db down: secret-path")

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(auth, "SessionLocal", lambda: Broken())
        r = c.get("/healthz")
    assert r.status_code == 503 and r.json() == {"ok": False, "db": "error"}  # 원인 문자열은 내보내지 않음


# ------------------------------------------------------------------ 매일 백업 · 정리
def test_daily_maintenance(db, user, tmp_path, monkeypatch):
    url = settings.database_url
    bdir = Path(backup_dir(url))
    bdir.mkdir(parents=True, exist_ok=True)
    old = bdir / "app-before-0005-20260101-000000.db"
    old.write_bytes(b"x")
    stale = _time.time() - 31 * 86400
    os.utime(old, (stale, stale))
    recent = bdir / "app-daily-recent.db"
    recent.write_bytes(b"x")
    now, today = clock.now(), clock.today()
    db.add_all([
        LoginRequest(handoff="h1", state="s1", code_verifier="v", oidc_nonce="n", created_at=now - timedelta(days=2)),
        LoginRequest(handoff="h2", state="s2", code_verifier="v", oidc_nonce="n", created_at=now - timedelta(hours=1)),
        NotifyLog(user_id=user.id, date=today - timedelta(days=91), kind="daily", channel="push", sent_at=now, status="sent", attempts=1),
        NotifyLog(user_id=user.id, date=today - timedelta(days=89), kind="daily", channel="push", sent_at=now, status="sent", attempts=1),
        DailyCounter(day=today - timedelta(days=31), key="push_removed", count=1),
        DailyCounter(day=today - timedelta(days=29), key="push_removed", count=1),
    ])
    db.commit()
    line = maintenance.run_daily(db, url)
    names = [n for n, _ in list_backups(url)]
    assert not old.exists() and recent.exists()
    assert any(n.startswith("app-daily-") and n != recent.name for n in names)  # 오늘 백업 생성
    db.expire_all()
    assert [r.handoff for r in db.scalars(select(LoginRequest))] == ["h2"]
    assert len(db.scalars(select(NotifyLog)).all()) == 1
    assert len(db.scalars(select(DailyCounter)).all()) == 1
    assert "오래된 백업 삭제=1" in line and "login_requests=1" in line and "@" not in line


def test_maintenance_job_is_scheduled(monkeypatch):
    from app import scheduler

    monkeypatch.setattr(scheduler.BackgroundScheduler, "start", lambda self: None)
    monkeypatch.setattr(scheduler, "_scheduler", None)  # 테스트가 끝나면 원래대로 (실행 중이 아닌 스케줄러를 남기지 않게)
    scheduler.start()
    jobs = {j.id: j for j in scheduler._scheduler.get_jobs()}
    assert set(jobs) == {"notify", "maintenance"}
    assert "hour='4'" in str(jobs["maintenance"].trigger) and "minute='17'" in str(jobs["maintenance"].trigger)


# ------------------------------------------------------------------ 로그에 비밀값·이메일 없음
def test_startup_log_presence_only(monkeypatch, capsys):
    secrets = {
        "SECRET_KEY": "sk-very-secret", "GOOGLE_CLIENT_SECRET": "gcs-very-secret",
        "VAPID_PRIVATE_KEY": "vapid-very-secret", "ADMIN_EMAIL": "boss@example.com",
    }
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("VAPID_SUBJECT", raising=False)
    line = env_presence_line()
    assert "SECRET_KEY=설정됨" in line and "VAPID_SUBJECT=없음" in line
    with TestClient(app):
        pass
    out = capsys.readouterr().out + line
    for v in secrets.values():
        assert v not in out
    for gone in ("GMAIL", "MAIL_BACKEND"):
        assert gone not in line


def test_scheduler_lines_have_no_email(db, user, pusher, capsys):
    from app import scheduler

    setup_day(db, user)
    add_sub(db, user, "https://web.push.apple.com/me")
    monkey = datetime.combine(MON, time(7))
    import app.scheduler as s

    orig = s.clock.now
    s.clock.now = lambda: monkey
    try:
        scheduler.tick()
    finally:
        s.clock.now = orig
    out = capsys.readouterr().out
    assert "[scheduler]" in out and "user=" in out and "@" not in out


# ------------------------------------------------------------------ 리허설 스크립트
def test_rehearsal_script_runs_on_old_backup(tmp_path):
    path = tmp_path / "prod-backup.db"
    url = f"sqlite:///{path}"
    env = {**os.environ, "ADMIN_EMAIL": ""}
    subprocess.run([sys.executable, "-c", f"from alembic import command; from scripts import migrate; "
                    f"command.upgrade(migrate.alembic_config({url!r}), '0005')"], cwd=ROOT, env=env, check=True)
    r = subprocess.run([sys.executable, "-m", "scripts.rehearse_migrations", str(path)], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "현재 0005 → 적용할 단계: 0006, 0007" in r.stdout and "결과: 문제 없음" in r.stdout
    assert path.exists() and json.dumps(r.stdout).count("@") == 0
