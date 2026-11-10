"""사용자 간 데이터 격리."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.deps import current_user
from app.main import app
from app.models import Assignment, CalendarDay, Task, User
from app.services.allocator import generate_week, pending_items
from app.services.progress import week_progress
from app.services.users import delete_user, get_or_create_user

MON = date(2026, 11, 2)


def fill(db, u, name):
    for i in range(7):
        db.add(CalendarDay(user_id=u.id, date=MON + timedelta(days=i), shift_code="DAY"))
    db.add(Task(user_id=u.id, name=name, subject="S", duration_min=30, weekly_count=3))
    db.commit()
    generate_week(db, u.id, MON, MON)


@pytest.fixture()
def two(db, user):
    other = get_or_create_user(db, "other@example.com")
    fill(db, user, "MINE")
    fill(db, other, "THEIRS")
    return user, other


def test_services_only_see_own_rows(db, two):
    me, other = two
    mine = db.scalars(select(Assignment).where(Assignment.user_id == me.id)).all()
    assert mine and all(a.task.name == "MINE" for a in mine)
    db.get(Assignment, mine[0].id).status = "missed"
    db.commit()
    assert len(pending_items(db, me.id)) == 1
    assert pending_items(db, other.id) == []
    assert week_progress(db, other.id, MON) == 0


@pytest.fixture()
def client_as(db):
    """로그인한 뒤 current_user 를 특정 사용자로 바꿔 끼운다."""
    def make(u: User):
        c = TestClient(app)
        c.__enter__()
        c.post("/login", data={"password": "pw"})
        app.dependency_overrides[current_user] = lambda: db.get(User, u.id)
        return c

    yield make
    app.dependency_overrides.clear()


def test_http_cannot_touch_other_users_data(db, two, client_as):
    me, other = two
    their_task = db.scalar(select(Task).where(Task.user_id == other.id))
    their_a = db.scalar(select(Assignment).where(Assignment.user_id == other.id))
    c = client_as(me)

    assert "THEIRS" not in c.get("/tasks").text
    assert "THEIRS" not in c.get(f"/?start={MON}").text
    assert c.get(f"/tasks?edit={their_task.id}").status_code == 404
    assert c.post(f"/tasks/{their_task.id}/toggle").status_code == 404
    r = c.post("/tasks", data={"task_id": their_task.id, "name": "X", "duration_min": 1, "weekly_count": 1})
    assert r.status_code == 404
    assert c.post(f"/assignments/{their_a.id}/status", data={"status": "done"}).status_code == 404

    db.expire_all()
    assert db.get(Task, their_task.id).name == "THEIRS" and db.get(Task, their_task.id).active
    assert db.get(Assignment, their_a.id).status == "scheduled"

    # 내 달력 저장이 다른 사람 달력을 건드리지 않음
    c.post("/calendar", data={"month": "2026-11"})
    db.expire_all()
    assert db.scalar(select(CalendarDay).where(CalendarDay.user_id == other.id, CalendarDay.date == MON))
    c.__exit__(None, None, None)


def test_backup_is_admin_only(db, two, client_as):
    me, other = two
    c = client_as(other)
    assert c.get("/admin/backup").status_code == 403
    c.__exit__(None, None, None)


def test_delete_user_removes_everything(db, two):
    me, other = two
    oid = other.id
    delete_user(db, other)
    for model in (Task, Assignment, CalendarDay):
        assert db.scalar(select(model).where(model.user_id == oid)) is None
    assert db.scalar(select(Task).where(Task.user_id == me.id)) is not None
