"""브랜드 푸시 문구: 묶음 선택 · 날짜별 순환 · 같은 날 고정 · 사용자별 위치 · 길이 · 기본 문구 폴백.
그리고 일요일이 다른 요일과 같은 날로 취급되는지."""
import json
import re
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


# ------------------------------------------------------------------ 묶음별 선택 (쉬는 날 체크 여부)
def test_bundle_follows_rest_day_flag(db, user, pusher):
    put(db, user, MON, "DAY")
    put(db, user, MON + timedelta(1), "OFF")  # 프리셋 오프 = 쉬는 날
    add_sub(db, user, "https://web.push.apple.com/me")
    for d in (MON, MON + timedelta(1)):
        day = db.scalar(select(ShiftType).where(ShiftType.user_id == user.id, ShiftType.code == ("DAY" if d == MON else "OFF")))
        notify_due(db, user, datetime.combine(d, day.send_time))
    work, rest = sent(pusher)
    assert work["title"] == rest["title"] == T.PUSH_TITLE == "afterwork."
    assert work["body"] == T.pick(T.WORKDAY_DAILY, MON, user.id) and work["body"] in T.WORKDAY_DAILY
    assert rest["body"] == T.pick(T.RESTDAY_DAILY, MON + timedelta(1), user.id) and rest["body"] in T.RESTDAY_DAILY
    assert all(p["url"] == "/today" for p in (work, rest))
    assert not re.search(r"\d+\s*(개|분)", work["body"] + rest["body"])  # 과제 개수·시간 없음


def test_remind_uses_remind_bundle(db, user, pusher):
    put(db, user, MON, "DAY")
    add_sub(db, user, "https://web.push.apple.com/me")
    notify_due(db, user, datetime.combine(MON, time(6)))
    remind_due(db, user, datetime.combine(MON, time(12)))
    daily, remind = sent(pusher)
    assert remind["title"] == T.PUSH_TITLE and remind["body"] == T.pick(T.REMIND, MON, user.id)
    assert remind["tag"].startswith("remind-") and daily["tag"].startswith("daily-")


def test_unchecking_rest_flag_switches_to_workday_bundle(db, user):
    off = db.scalar(select(ShiftType).where(ShiftType.user_id == user.id, ShiftType.code == "OFF"))
    put(db, user, MON, "OFF")
    assert push_body(db, user, MON, "daily") in T.RESTDAY_DAILY
    off.is_rest_day = False
    db.commit()
    db.expire_all()
    assert push_body(db, user, MON, "daily") in T.WORKDAY_DAILY


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
    """재호출·재시도·여러 기기에서도 같은 날 같은 사용자는 같은 문구."""
    put(db, user, MON, "DAY")
    add_sub(db, user, "https://web.push.apple.com/a")
    add_sub(db, user, "https://fcm.googleapis.com/b")
    pusher["fail"]["https://fcm.googleapis.com/b"] = 503
    notify_due(db, user, datetime.combine(MON, time(6)))  # 기기 2대 (하나는 실패)
    pusher["fail"].clear()
    with TestClient(app) as c:
        login(c)
        c.post("/push/test")
        c.post("/push/test")
    daily_bodies = {p["body"] for p in sent(pusher) if p["tag"].startswith("daily-")}
    assert len(daily_bodies) == 1  # 같은 날 정기 알림은 모든 기기·재시도에서 같은 문구
    assert {push_body(db, user, MON, "daily") for _ in range(5)} == daily_bodies


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
def test_test_push_uses_brand_title_and_workday_phrase(db, user, pusher):
    add_sub(db, user, "https://web.push.apple.com/me")
    with TestClient(app) as c:
        login(c)
        assert c.post("/push/test").json()["ok"]
    data = sent(pusher)[-1]
    from app import clock

    assert data["title"] == "afterwork." and data["body"] == T.pick(T.WORKDAY_DAILY, clock.today(), user.id)


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
    assert push_body(db, user, SUN, "daily") in T.WORKDAY_DAILY  # 일요일 데이 근무 = 근무한 날 문구


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
