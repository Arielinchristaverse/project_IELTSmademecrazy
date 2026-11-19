"""ADMIN_EMAIL 변경: 새 주소만 관리자, 예전 관리자는 권한 해제, 데이터는 그대로."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import Task, User
from app.services.users import get_or_create_user, sync_admin_flags
from tests.conftest import login

OLD, NEW = "me@example.com", "service@example.com"


def admins(db):
    db.expire_all()
    return sorted(u.email for u in db.scalars(select(User).where(User.is_admin.is_(True))))


def test_startup_sync_moves_admin_and_keeps_data(db, user, monkeypatch, capsys):
    db.add(Task(user_id=user.id, name="기존 과제", subject="", duration_min=30, weekly_count=2, active=True))
    db.commit()
    assert admins(db) == [OLD]
    monkeypatch.setattr(settings, "admin_email", NEW)
    with TestClient(app) as c:  # 서버 시작 = 동기화
        assert admins(db) == []  # 예전 관리자 해제 (새 계정은 아직 없음)
        login(c, OLD)
        assert c.get("/admin/backup").status_code == 403  # 다시 로그인해도 관리자로 돌아오지 않음
        assert "기존 과제" in c.get("/tasks").text  # 데이터는 그대로
        assert admins(db) == []
        c.post("/logout")
        login(c, NEW)  # 새 주소 첫 로그인 → 관리자
        assert c.get("/admin/backup").status_code != 403
    assert admins(db) == [NEW]
    out = capsys.readouterr().out
    assert "[admin] 관리자 동기화: 부여 0 · 해제 1" in out
    assert OLD not in out and NEW not in out  # 이메일은 로그에 남기지 않음


def test_sync_grants_existing_account(db, user, monkeypatch):
    other = get_or_create_user(db, NEW)
    assert not other.is_admin
    monkeypatch.setattr(settings, "admin_email", NEW)
    assert sync_admin_flags(db) == (1, 1)
    assert admins(db) == [NEW]


def test_rollback_restores_old_admin(db, user, monkeypatch):
    get_or_create_user(db, NEW)
    monkeypatch.setattr(settings, "admin_email", NEW)
    sync_admin_flags(db)
    monkeypatch.setattr(settings, "admin_email", OLD)  # 원래대로 되돌리고 재시작
    assert sync_admin_flags(db) == (1, 1)
    assert admins(db) == [OLD]


def test_empty_admin_email_changes_nothing(db, user, monkeypatch):
    monkeypatch.setattr(settings, "admin_email", "")
    assert sync_admin_flags(db) is None
    assert admins(db) == [OLD]
    login_user = get_or_create_user(db, OLD)  # 로그인해도 그대로
    assert login_user.is_admin
