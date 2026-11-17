"""푸시 문구: 첫 알림 = 과제 요약, 재알림 = 브랜드 문구(날짜별 순환·같은 날 고정·사용자별 위치) · 길이 · 기본 문구 폴백.
그리고 일요일이 다른 요일과 같은 날로 취급되는지."""
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Assignment, CalendarDay, ShiftType, Task
from app.services import notify_text as T
from app.services import push
from app.services.allocator import generate_week, is_rest_day, pending_items
from app.services.daily import notify_due, prepare_day, push_body, remind_due
from app.services.users import get_or_create_user
from tests.conftest import login
from tests.test_push import add_sub, pusher  # noqa: F401  (pusher 는 fixture)

MON = date(2026, 11, 2)
SUN = MON + timedelta(days=6)
ROOT = Path(__file__).resolve().parent.parent


def put(db, user, d, code, tasks=True):
    db.add(CalendarDay(user_id=user.id, date=d, shift_code=code))
    if tasks and db.scalar(select(Task).where(Task.user_id == user.id)) is None:
        db.add(Task(user_id=user.id, name="리딩", subject="R", duration_min=30, weekly_count=7))
    user.notify_channel = "push"
    user.onboarding_step = 3
    db.commit()


def sent(box):
    return [json.loads(c["data"]) for c in box["calls"]]


# ------------------------------------------------------------------ 첫 알림 = 과제 요약 (브랜드 문구 없음)
@pytest.mark.parametrize("code", ["DAY", "OFF"])  # 근무한 날이든 쉬는 날이든
def test_first_push_is_task_summary_without_brand_phrase(db, user, pusher, code):
    put(db, user, MON, code)
    add_sub(db, user, "https://web.push.apple.com/me")
    day = db.scalar(select(ShiftType).where(ShiftType.user_id == user.id, ShiftType.code == code))
    notify_due(db, user, datetime.combine(MON, day.send_time))
    (data,) = sent(pusher)
    todays = db.scalars(select(Assignment).where(Assignment.user_id == user.id, Assignment.date == MON)).all()
    assert data["title"] == T.PUSH_TITLE == "afterwork." and data["url"] == "/today"
    assert data["body"] == T.DAILY_PUSH_BODY.format(count=len(todays), minutes=sum(a.task.duration_min for a in todays))
    assert data["body"] not in T.WORKDAY_DAILY + T.RESTDAY_DAILY + T.REMIND  # 인사말은 첫 알림에 없음


def test_remind_uses_remind_bundle(db, user, pusher):
    put(db, user, MON, "DAY")
    add_sub(db, user, "https://web.push.apple.com/me")
    notify_due(db, user, datetime.combine(MON, time(6)))
    remind_due(db, user, datetime.combine(MON, time(12)))
    daily, remind = sent(pusher)
    assert remind["title"] == T.PUSH_TITLE and remind["body"] == T.pick(T.REMIND, MON, user.id)
    assert remind["tag"].startswith("remind-") and daily["tag"].startswith("daily-")


# ------------------------------------------------------------------ 순환
def test_next_day_moves_to_next_phrase_and_wraps():
    uid = 7
    seq = [T.pick(T.WORKDAY_DAILY, MON + timedelta(i), uid) for i in range(len(T.WORKDAY_DAILY) + 1)]
    start = T.WORKDAY_DAILY.index(seq[0])
    expected = [T.WORKDAY_DAILY[(start + i) % len(T.WORKDAY_DAILY)] for i in range(len(seq))]
    assert seq == expected  # 하루에 하나씩 다음 문구, 끝에서 처음으로
    assert seq[-1] == seq[0]
    for bundle in (T.RESTDAY_DAILY, T.REMIND):
        a, b = T.pick(bundle, MON, uid), T.pick(bundle, MON + timedelta(1), uid)
        assert bundle.index(b) == (bundle.index(a) + 1) % len(bundle)


def test_same_day_same_user_always_same_phrase(db, user, pusher):
    """재알림 문구는 재시도·여러 기기·반복 호출에서도 같은 날 같은 사용자에게 같다."""
    put(db, user, MON, "DAY")
    add_sub(db, user, "https://web.push.apple.com/a")
    add_sub(db, user, "https://fcm.googleapis.com/b")
    notify_due(db, user, datetime.combine(MON, time(6)))
    pusher["fail"]["https://web.push.apple.com/a"] = 503  # 첫 시도: 한 기기 실패
    pusher["fail"]["https://fcm.googleapis.com/b"] = 503
    remind_due(db, user, datetime.combine(MON, time(12)))
    pusher["fail"].clear()
    remind_due(db, user, datetime.combine(MON, time(12, 11)))  # 10분 뒤 재시도 → 두 기기 성공
    bodies = {p["body"] for p in sent(pusher) if p["tag"].startswith("remind-")}
    assert bodies == {T.pick(T.REMIND, MON, user.id)}
    assert {push_body(user, MON, "remind", []) for _ in range(5)} == bodies


def test_users_have_different_positions(db, user):
    others = [get_or_create_user(db, f"u{i}@example.com") for i in range(len(T.WORKDAY_DAILY))]
    ids = [user.id] + [o.id for o in others]
    picks = [T.pick(T.WORKDAY_DAILY, MON, uid) for uid in ids]
    assert picks[0] != picks[1]  # 이웃한 사용자는 같은 날 다른 문구
    assert len(set(picks)) == len(T.WORKDAY_DAILY)  # 사용자마다 시작 위치가 고르게 퍼짐


# ------------------------------------------------------------------ 길이
def test_all_phrases_short_and_payload_under_200_bytes():
    for phrase in T.WORKDAY_DAILY + T.RESTDAY_DAILY + T.REMIND + (T.FALLBACK_BODY, T.PUSH_TITLE):
        assert "\n" not in phrase and len(phrase) <= 30, phrase
        for kind in ("daily", "remind"):
            data = push.payload(T.PUSH_TITLE, phrase, url="/today", tag=f"{kind}-2026-12-31")
            assert len(data.encode()) < 200, (phrase, len(data.encode()))


# ------------------------------------------------------------------ 테스트 알림 · 기본 문구 폴백
def test_test_push_matches_first_push_format(db, user, pusher):
    add_sub(db, user, "https://web.push.apple.com/me")
    with TestClient(app) as c:
        login(c)
        assert c.post("/push/test").json()["ok"]
    data = sent(pusher)[-1]
    assert data["title"] == "afterwork."
    assert data["body"] == T.DAILY_PUSH_EMPTY or data["body"].startswith("오늘의 공부 과제 ")  # 과제 요약


def test_service_worker_fallback_matches_constants():
    js = (ROOT / "app/static/sw.js").read_text(encoding="utf-8")
    assert f'const DEFAULT_TITLE = "{T.PUSH_TITLE}";' in js
    assert f'const DEFAULT_BODY = "{T.FALLBACK_BODY}";' in js
    assert T.FALLBACK_BODY in T.WORKDAY_DAILY
    # 빈/깨진 내용에서 실제로 이 기본 문구를 띄우는지는 tests/js/sw.test.js 가 Node 로 확인


# ------------------------------------------------------------------ 일요일 = 다른 요일과 같은 날
def test_sunday_is_not_special(db, user, pusher):
    for i in range(7):
        put(db, user, MON + timedelta(i), "DAY")  # 일요일 포함 전부 데이
    generate_week(db, user.id, MON, MON)
    first = db.scalar(select(Assignment).where(Assignment.user_id == user.id, Assignment.date == MON))
    first.status = "missed"
    db.commit()
    sun_cal = db.scalar(select(CalendarDay).where(CalendarDay.user_id == user.id, CalendarDay.date == SUN))
    assert not is_rest_day(SUN, sun_cal)
    prepare_day(db, user, SUN)
    assert all(a.kind == "regular" for a in db.scalars(select(Assignment).where(Assignment.date == SUN)))
    assert first.id in [p.id for p in pending_items(db, user.id)]  # 보충은 다음 쉬는 날로 넘어감


@pytest.mark.parametrize("rest_day_index", [2, 5, 6])  # 수·토·일 어느 요일이든 쉬는 날이면 보충일
def test_any_weekday_with_rest_type_is_makeup_day(db, user, rest_day_index):
    for i in range(7):
        put(db, user, MON + timedelta(i), "OFF" if i == rest_day_index else "DAY")
    generate_week(db, user.id, MON, MON)
    first = db.scalar(select(Assignment).where(Assignment.user_id == user.id, Assignment.date == MON))
    first.status = "missed"
    db.commit()
    rest = MON + timedelta(rest_day_index)
    prepare_day(db, user, rest)
    makeups = [a for a in db.scalars(select(Assignment).where(Assignment.date == rest)) if a.kind == "makeup"]
    assert [m.makeup_of_id for m in makeups] == [first.id]


def test_calendar_has_no_sunday_styling(db, user):
    with TestClient(app) as c:
        login(c)
        html = c.get("/calendar?month=2026-11").text
    assert 'class="sun"' not in html and "num sun" not in html
    assert ".sun" not in (ROOT / "app/static/style.css").read_text(encoding="utf-8")
