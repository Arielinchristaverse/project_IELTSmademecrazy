from datetime import date, datetime, time, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import ActionToken, Assignment, CalendarDay, MailLog, Task
from app.services.allocator import generate_week, pending_items
from app.services.daily import prepare_day, send_daily_if_due, send_test_mail
from app.services.progress import week_progress

MON = date(2026, 11, 2)


def setup_month(db, shifts):
    for i, code in enumerate(shifts):
        db.add(CalendarDay(date=MON + timedelta(days=i), shift_code=code))
    db.add_all([
        Task(name="Reading", subject="R", duration_min=60, weekly_count=3),
        Task(name="Vocab", subject="V", duration_min=30, weekly_count=5),
    ])
    db.commit()


def day_items(db, d):
    return db.scalars(select(Assignment).where(Assignment.date == d)).all()


def test_generate_and_auto_miss_and_weekend_makeup(db):
    # 월~토 데이(60분), 수요일은 오프(180분), 일요일 오프
    setup_month(db, ["DAY", "DAY", "OFF", "DAY", "DAY", "DAY", "OFF"])
    generate_week(db, MON, MON)
    mon_items = day_items(db, MON)
    assert mon_items and sum(a.task.duration_min for a in mon_items) <= 60

    # 월요일 기록 안 함 → 화요일 메일 준비 시 자동 미달성
    prepare_day(db, MON + timedelta(days=1))
    assert all(a.status == "missed" for a in day_items(db, MON))
    assert len(pending_items(db)) == len(mon_items)

    # 수요일은 오프 → 보충 과제가 먼저 배정
    wed = MON + timedelta(days=2)
    prepare_day(db, wed)
    wed_items = sorted(day_items(db, wed), key=lambda a: a.position)
    assert wed_items[0].kind == "makeup"
    assert pending_items(db) == []


def test_mail_buttons_record_once(db):
    setup_month(db, ["DAY"] * 5 + ["OFF", "OFF"])
    client = TestClient(__import__("app.main", fromlist=["app"]).app)
    send_test_mail(db, MON)
    toks = db.scalars(select(ActionToken)).all()
    assert toks
    done_tok = next(t for t in toks if t.action == "done")
    r = client.get(f"/a/{done_tok.token}")
    assert r.status_code == 200 and "달성 기록 완료" in r.text
    # 같은 배정의 '미달성' 링크는 더 이상 동작하지 않음
    other = next(t for t in toks if t.assignment_id == done_tok.assignment_id and t.action == "missed")
    r2 = client.get(f"/a/{other.token}")
    assert "이미 기록됨" in r2.text
    db.expire_all()
    assert week_progress(db, MON) > 0
    assert client.get("/a/nope").status_code == 404


def test_makeup_done_marks_original_done(db):
    setup_month(db, ["DAY"] * 5 + ["DAY", "OFF"])
    generate_week(db, MON, MON)
    original = day_items(db, MON)[0]
    original.status = "missed"
    db.commit()
    sun = MON + timedelta(days=6)
    prepare_day(db, sun)
    makeup = next(a for a in day_items(db, sun) if a.kind == "makeup")
    from app.services.progress import set_status

    before = week_progress(db, MON)
    set_status(db, makeup, "done")
    db.refresh(original)
    assert original.status == "done"
    assert week_progress(db, MON) > before


def test_scheduler_sends_once_after_send_time(db):
    setup_month(db, ["DAY"] * 7)
    t = datetime.combine(MON, time(5, 0))
    assert send_daily_if_due(db, t) is None  # 발송 시각(05:30) 전
    assert send_daily_if_due(db, t.replace(hour=6)) is not None
    assert send_daily_if_due(db, t.replace(hour=7)) is None  # 하루 한 번
    assert db.scalar(select(MailLog)).status == "sent"


def test_excluded_day_no_mail(db):
    setup_month(db, ["DAY"] * 7)
    db.get(CalendarDay, MON).excluded = True
    db.commit()
    assert send_daily_if_due(db, datetime.combine(MON, time(9, 0))) is None


def test_login_required(db):
    from app.main import app

    with TestClient(app) as c:
        assert c.get("/", follow_redirects=False).status_code == 303
        c.post("/login", data={"password": "pw"})
        for path in ["/", "/calendar", "/tasks", "/settings", "/sentences"]:
            assert c.get(path).status_code == 200, path


def test_future_week_does_not_take_pending(db):
    setup_month(db, ["DAY", "DAY", "DAY", "DAY", "DAY", "OFF", "OFF"] * 2)
    generate_week(db, MON, MON)
    a = day_items(db, MON)[0]
    a.status = "missed"
    db.commit()
    generate_week(db, MON + timedelta(days=7), MON)  # 다음 주를 미리 생성
    assert len(pending_items(db)) == 1  # 이번 주 주말을 위해 남아 있어야 함
    sat = MON + timedelta(days=5)
    prepare_day(db, sat)  # 이번 주 토요일(오프): 가장 오래된 보류가 먼저 보충됨
    makeups = [x for x in day_items(db, sat) if x.kind == "makeup"]
    assert a.id in {m.makeup_of_id for m in makeups}
    next_sat = [x for x in day_items(db, sat + timedelta(days=7)) if x.kind == "makeup"]
    assert next_sat == []
