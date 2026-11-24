"""여유가 생기면 해볼 과제: 시간 부족으로 못 넣은 과제의 추가 달성 기록."""
import sqlite3
from datetime import date, datetime, time, timedelta

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.main import app
from app.models import Assignment, CalendarDay, ExtraCompletion, Task, WeekShortfall
from app.services.allocator import generate_week, pending_items
from app.services.daily import day_assignments, prepare_day, push_body, remaining
from app.services.extras import week_extras
from app.services.progress import set_status, week_progress
from app.services.users import get_or_create_user
from scripts import migrate
from tests.conftest import login

MON = date(2026, 11, 2)
WED = MON + timedelta(days=2)


@pytest.fixture()
def today(monkeypatch):
    """서버의 '오늘'(Asia/Seoul)을 바꾼다. 기본은 이번 주 수요일."""
    box = {"d": WED}
    monkeypatch.setattr(clock, "today", lambda: box["d"])
    monkeypatch.setattr(clock, "now", lambda: datetime.combine(box["d"], time(20, 0)))
    return box


def setup_week(db, u, weekend_excluded=True):
    """월~금 데이(60분), 주말 제외 → 60분 과제 주 7회 중 2회를 못 넣는다."""
    for i in range(7):
        db.add(CalendarDay(user_id=u.id, date=MON + timedelta(days=i), shift_code="DAY", excluded=weekend_excluded and i >= 5))
    task = Task(user_id=u.id, name="모의고사", subject="R", duration_min=60, weekly_count=7)
    db.add(task)
    db.commit()
    generate_week(db, u.id, MON, MON)
    return task


def extras_in_db(db, u):
    db.expire_all()
    return db.scalars(select(ExtraCompletion).where(ExtraCompletion.user_id == u.id)).all()


def post(c, tid, action, ws=MON):
    return c.post(f"/extras/{tid}/{action}", data={"week_start": ws.isoformat()})


def test_shortfall_saved_with_generation(db, user):
    task = setup_week(db, user)
    rows = db.scalars(select(WeekShortfall).where(WeekShortfall.user_id == user.id)).all()
    assert [(r.task_id, r.count, r.week_start) for r in rows] == [(task.id, 2, MON)]
    (it,) = week_extras(db, user.id, MON)
    assert (it.shortfall, it.recorded, it.remaining) == (2, 0, 2)


def test_extra_raises_progress_and_page(db, user, today):
    task = setup_week(db, user)
    assert week_progress(db, user.id, MON) == 0
    with TestClient(app) as c:
        login(c)
        page = c.get(f"/?start={MON}").text
        assert "여유가 생기면 해볼 과제" in page and "⚠" not in page
        assert 'aria-label="모의고사 추가 달성 기록 (남은 2회)"' in page
        assert "남은 2회 · 못 넣은 2회" in page
        r = post(c, task.id, "done")
        assert r.status_code == 200 and r.url.fragment == "extras"
        page = r.text
    assert week_progress(db, user.id, MON) == 14  # 1 / 7
    assert '<div class="stat">14<span class="pct">%</span></div>' in page
    assert 'aria-valuenow="14"' in page and 'style="width: 14%"' in page  # 막대
    assert "남은 1회 · 못 넣은 2회 · 기록 1회" in page
    (row,) = extras_in_db(db, user)
    assert row.date == WED and row.week_start == MON  # 누른 날 기록


def test_cannot_exceed_shortfall(db, user, today):
    task = setup_week(db, user)
    with TestClient(app) as c:
        login(c)
        post(c, task.id, "done")
        post(c, task.id, "done")
        page = post(c, task.id, "done").text
    assert len(extras_in_db(db, user)) == 2
    assert "못 넣은 횟수만큼 이미 기록했어요." in page
    assert "완료 · 2회 기록" in page and "추가 달성 기록 (남은" not in page  # 완료 표시, 체크 버튼 없음
    assert week_progress(db, user.id, MON) == 29  # 2 / 7


def test_undo(db, user, today):
    task = setup_week(db, user)
    with TestClient(app) as c:
        login(c)
        post(c, task.id, "done")
        post(c, task.id, "done")
        page = post(c, task.id, "undo").text
        assert len(extras_in_db(db, user)) == 1 and week_progress(db, user.id, MON) == 14
        assert 'aria-label="모의고사 추가 달성 1회 되돌리기"' in page
        post(c, task.id, "undo")
        page = post(c, task.id, "undo").text  # 되돌릴 것 없음
    assert extras_in_db(db, user) == [] and week_progress(db, user.id, MON) == 0
    assert "되돌릴 기록이 없어요." in page


def test_past_and_future_weeks_are_read_only(db, user, today):
    task = setup_week(db, user)
    with TestClient(app) as c:
        login(c)
        post(c, task.id, "done")
        today["d"] = MON + timedelta(days=7)  # 다음 주 월요일이 됨
        page = post(c, task.id, "done").text
        assert "이번 주 항목만 기록하거나 되돌릴 수 있어요." in page
        post(c, task.id, "undo")
        assert len(extras_in_db(db, user)) == 1  # 지난 주 기록은 그대로
        page = c.get(f"/?start={MON}").text
        assert "이번 주가 아니라서 기록하거나 되돌릴 수 없어요." in page
        assert 'action="/extras/' not in page  # 체크·되돌리기 버튼 없음
        today["d"] = WED
        post(c, task.id, "done", ws=MON + timedelta(days=7))  # 다음 주 항목도 거부
    assert len(extras_in_db(db, user)) == 1


def test_isolation(db, user, today):
    task = setup_week(db, user)
    other = get_or_create_user(db, "other@example.com")
    other.onboarding_step = 3
    db.commit()
    with TestClient(app) as c:
        login(c, "other@example.com")
        assert post(c, task.id, "done").status_code == 404
        assert post(c, task.id, "undo").status_code == 404
        assert "모의고사" not in c.get(f"/?start={MON}").text
    assert extras_in_db(db, user) == []
    assert week_extras(db, other.id, MON) == []


def test_records_kept_after_regeneration_and_progress_capped(db, user, today):
    task = setup_week(db, user)
    with TestClient(app) as c:
        login(c)
        post(c, task.id, "done")
        post(c, task.id, "done")
        # 토요일 제외를 풀고 다시 배정 → 못 넣은 횟수 2 → 1
        sat = db.scalar(select(CalendarDay).where(CalendarDay.user_id == user.id, CalendarDay.date == MON + timedelta(days=5)))
        sat.excluded = False
        db.commit()
        c.post("/weeks/generate", data={"start": MON.isoformat()})
        page = c.get(f"/?start={MON}").text
    assert len(extras_in_db(db, user)) == 2  # 기록 유지
    (it,) = week_extras(db, user.id, MON)
    assert (it.shortfall, it.recorded, it.over, it.done) == (1, 2, True, True)
    assert "기록 2회 · 다시 배정되면서 못 넣은 횟수가 1회로 줄었어요" in page
    assert week_progress(db, user.id, MON) == 29  # 정규 0 + 추가 2
    for a in db.scalars(select(Assignment).where(Assignment.user_id == user.id)):
        set_status(db, a, "done")
    # 정규 6 + 추가는 (7-6)=1 까지만 → 7/7, 부풀지 않음
    assert week_progress(db, user.id, MON) == 100


def test_today_notifications_and_pending_unaffected(db, user, today):
    task = setup_week(db, user)
    prepare_day(db, user, WED)  # 오늘 화면을 여는 것과 같은 준비(월·화 미기록 → 보류)를 먼저
    a = day_assignments(db, user.id, WED)
    before = {
        "today": [(x.id, x.status) for x in a],
        "pending": [x.id for x in pending_items(db, user.id)],
        "remaining": len(remaining(a)),
        "push": push_body(user, WED, "daily", a),
        "remind": push_body(user, WED, "remind", a),
    }
    with TestClient(app) as c:
        login(c)
        post(c, task.id, "done")
        today_page = c.get("/today").text
    db.expire_all()
    a = day_assignments(db, user.id, WED)
    after = {
        "today": [(x.id, x.status) for x in a],
        "pending": [x.id for x in pending_items(db, user.id)],
        "remaining": len(remaining(a)),
        "push": push_body(user, WED, "daily", a),
        "remind": push_body(user, WED, "remind", a),
    }
    assert after == before
    assert "여유가 생기면 해볼 과제" not in today_page
    assert len(db.scalars(select(Assignment).where(Assignment.user_id == user.id)).all()) == 5  # 배정 수 그대로


# ------------------------------------------------------------------ 마이그레이션 0005 → 0006
def test_migration_backfills_from_warning_text(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)  # 빈 DB 에 관리자 행을 미리 만들지 않게
    path = tmp_path / "app.db"
    url = f"sqlite:///{path}"
    command.upgrade(migrate.alembic_config(url), "0005")
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO users (id, email, google_sub, name, timezone, notify_channel, notify_email, sentence_cursor, "
        "onboarding_step, last_prepared_date, is_admin, created_at, last_login_at) "
        "VALUES (1, 'me@example.com', 'g', '', 'Asia/Seoul', 'push', '', 0, 3, NULL, 1, '2026-11-01 00:00:00.000000', NULL)"
    )
    for tid, name in [(1, "Reading: Part 1"), (2, "중복"), (3, "중복")]:
        con.execute(
            "INSERT INTO tasks (id, user_id, name, subject, duration_min, weekly_count, active) VALUES (?, 1, ?, '', 60, 5, 1)",
            (tid, name),
        )
    warnings = "\n".join([
        "Reading: Part 1: 이번 주 2회 미배정 (공부 가능 시간 부족)",  # 이름에 ':' 가 있어도 OK
        "중복: 이번 주 1회 미배정 (공부 가능 시간 부족)",  # 같은 이름 2개 → 건너뜀
        "없는 과제: 이번 주 3회 미배정 (공부 가능 시간 부족)",
        "형식이 다른 줄",
    ])
    con.execute("INSERT INTO weeks (id, user_id, start_date, generated_at, warnings) VALUES (1, 1, '2026-11-02', '2026-11-02 06:00:00.000000', ?)", (warnings,))
    con.commit()
    con.close()
    assert migrate.run(url).startswith("upgraded 0005 -> ")  # 0006 포함 최신까지
    con = sqlite3.connect(path)
    assert con.execute("select user_id, week_start, task_id, count from week_shortfalls").fetchall() == [(1, "2026-11-02", 1, 2)]
    assert con.execute("select count(*) from extra_completions").fetchone()[0] == 0
    assert con.execute("select warnings from weeks").fetchone()[0] == warnings  # 기존 데이터 그대로
    con.close()
    assert list((tmp_path / "backups").glob("app-before-*.db"))  # 자동 백업
