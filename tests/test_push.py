"""웹 푸시: 구독 API · 발송(모킹) · 만료 구독 삭제 · 재시도 · 사용자별 발송 시각."""
import json
from datetime import date, datetime, time, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import CalendarDay, NotifyLog, PushSubscription, ShiftType, Task
from app.services import notify_text as T
from app.services import push
from app.services.daily import notify_due, run_due
from app.services.users import get_or_create_user
from scripts.gen_vapid import generate
from tests.conftest import add_sub, login

MON = date(2026, 11, 2)


def setup_days(db, user, send_at: time):
    db.scalar(select(ShiftType).where(ShiftType.user_id == user.id, ShiftType.code == "DAY")).send_time = send_at
    for i in range(7):
        db.add(CalendarDay(user_id=user.id, date=MON + timedelta(days=i), shift_code="DAY"))
    db.add(Task(user_id=user.id, name="단어", subject="V", duration_min=30, weekly_count=5))
    user.onboarding_step = 3
    db.commit()


# ---------------------------------------------------------------- 구독 API
def test_subscribe_api(db, user):
    body = {"endpoint": "https://web.push.apple.com/abc", "keys": {"p256dh": "p" * 20, "auth": "a" * 16}}
    with TestClient(app) as c:
        assert c.post("/push/subscribe", json=body, follow_redirects=False).status_code == 303  # 로그인 필요
        login(c)
        assert c.post("/push/subscribe", data={"endpoint": "x"}).status_code in (415, 422)  # JSON 만
        assert c.post("/push/subscribe", json={**body, "endpoint": "http://insecure/x"}).status_code == 400
        assert c.post("/push/subscribe", json=body).json()["ok"]
        assert c.post("/push/subscribe", json=body).json()["ok"]  # 같은 기기 다시 등록 → 1개 유지
    subs = db.scalars(select(PushSubscription)).all()
    assert len(subs) == 1 and subs[0].user_id == user.id

    # 같은 기기에서 다른 계정으로 로그인해 등록하면 그 계정으로 옮겨짐
    other = get_or_create_user(db, "other@example.com")
    other.onboarding_step = 3
    db.commit()
    with TestClient(app) as c:
        login(c, "other@example.com")
        c.post("/push/subscribe", json=body)
        assert c.post(f"/push/devices/{subs[0].id}/delete").status_code in (303, 200)
    db.expire_all()
    assert db.scalars(select(PushSubscription)).all() == []


def test_cannot_delete_or_unsubscribe_other_users_device(db, user):
    other = get_or_create_user(db, "other@example.com")
    other.onboarding_step = 3
    db.commit()
    sub = add_sub(db, other, "https://fcm.googleapis.com/x")
    with TestClient(app) as c:
        login(c)
        assert c.post(f"/push/devices/{sub.id}/delete").status_code == 404
        c.post("/push/unsubscribe", json={"endpoint": sub.endpoint})
    db.expire_all()
    assert db.get(PushSubscription, sub.id) is not None


# ---------------------------------------------------------------- 발송
def test_send_to_all_devices_and_remove_expired(db, user, pusher):
    ok = add_sub(db, user, "https://web.push.apple.com/ok")
    add_sub(db, user, "https://web.push.apple.com/gone410")
    add_sub(db, user, "https://fcm.googleapis.com/gone404")
    err = add_sub(db, user, "https://updates.push.services.mozilla.com/err500")
    pusher["fail"].update({
        "https://web.push.apple.com/gone410": 410,
        "https://fcm.googleapis.com/gone404": 404,
        "https://updates.push.services.mozilla.com/err500": 500,
    })
    r = push.send_to_user(db, user, push.payload("t", "b"))
    assert (r.sent, r.removed, len(r.errors)) == (1, 2, 1)
    left = {s.endpoint for s in db.scalars(select(PushSubscription))}
    assert left == {ok.endpoint, err.endpoint}  # 404/410 만 삭제
    db.refresh(ok), db.refresh(err)
    assert ok.last_success_at is not None and "500" in err.last_error
    # VAPID 클레임은 기기마다 새로 만들어 전달 (aud 가 기기마다 다름)
    assert all(c["claims"] == {"sub": "mailto:test@example.com"} for c in pusher["calls"])
    assert pusher["calls"][0]["headers"] == {"Urgency": "high"}


def test_payload_is_short_and_points_to_today(db, user, pusher):
    setup_days(db, user, time(6, 0))
    add_sub(db, user, "https://web.push.apple.com/me")
    out = notify_due(db, user, datetime.combine(MON, time(6, 1)))
    assert out and out[0].startswith("push: 1대")
    data = json.loads(pusher["calls"][0]["data"])
    assert data["url"] == "/today"
    assert data["title"] == T.PUSH_TITLE
    assert data["body"].startswith("오늘의 공부 과제 ") and "· 총 " in data["body"]  # 첫 알림 = 과제 요약
    assert len(pusher["calls"][0]["data"].encode()) < 200


def test_retry_every_10_minutes_up_to_3_times(db, user, pusher):
    setup_days(db, user, time(6, 0))
    add_sub(db, user, "https://web.push.apple.com/flaky")
    pusher["fail"]["https://web.push.apple.com/flaky"] = 503
    t0 = datetime.combine(MON, time(6, 0))
    notify_due(db, user, t0)
    assert notify_due(db, user, t0 + timedelta(minutes=5)) == []  # 10분 전에는 재시도 안 함
    notify_due(db, user, t0 + timedelta(minutes=11))
    notify_due(db, user, t0 + timedelta(minutes=22))
    assert notify_due(db, user, t0 + timedelta(minutes=40)) == []  # 3회 후 중단
    log = db.scalar(select(NotifyLog).where(NotifyLog.channel == "push"))
    assert (log.status, log.attempts) == ("failed", 3)
    assert len(pusher["calls"]) == 3
    assert db.scalar(select(PushSubscription)) is not None  # 일시 오류로는 삭제 안 함


def test_all_devices_expired_is_not_retried(db, user, pusher):
    setup_days(db, user, time(6, 0))
    add_sub(db, user, "https://web.push.apple.com/old")
    pusher["fail"]["https://web.push.apple.com/old"] = 410
    t0 = datetime.combine(MON, time(6, 0))
    notify_due(db, user, t0)
    assert notify_due(db, user, t0 + timedelta(minutes=11)) == []
    log = db.scalar(select(NotifyLog).where(NotifyLog.channel == "push"))
    assert log.status == "skipped" and db.scalar(select(PushSubscription)) is None


def test_each_user_gets_push_at_own_time(db, user, pusher):
    """A는 06:00, B는 15:00 — 스케줄러가 전체 사용자를 돌며 각자 시각에 한 번씩."""
    b = get_or_create_user(db, "b@example.com")
    setup_days(db, user, time(6, 0))
    setup_days(db, b, time(15, 0))
    add_sub(db, user, "https://web.push.apple.com/a")
    add_sub(db, b, "https://web.push.apple.com/b")

    def sent_to():  # 정기 알림만 센다 (재알림은 tests/test_remind.py 에서 따로 검증)
        return [
            c["endpoint"].rsplit("/", 1)[1] for c in pusher["calls"] if json.loads(c["data"])["tag"].startswith("daily-")
        ]

    run_due(db, datetime.combine(MON, time(5, 59)))
    assert sent_to() == []
    run_due(db, datetime.combine(MON, time(6, 0)))
    assert sent_to() == ["a"]
    run_due(db, datetime.combine(MON, time(14, 59)))
    assert sent_to() == ["a"]
    run_due(db, datetime.combine(MON, time(15, 0)))
    run_due(db, datetime.combine(MON, time(16, 0)))
    assert sent_to() == ["a", "b"]


def test_push_only_and_sentence_advances_once_per_day(db, user, pusher):
    setup_days(db, user, time(6, 0))

    db.commit()
    add_sub(db, user, "https://web.push.apple.com/me")
    out = notify_due(db, user, datetime.combine(MON, time(6, 0)))
    assert [o.split(":")[0] for o in out] == ["push"]
    assert "@" not in " ".join(out)  # 결과(스케줄러 로그)에 이메일 없음
    db.refresh(user)
    day1 = user.sentence_cursor
    notify_due(db, user, datetime.combine(MON, time(9, 0)))  # 같은 날 다시 호출해도 그대로
    db.refresh(user)
    assert user.sentence_cursor == day1
    notify_due(db, user, datetime.combine(MON + timedelta(days=1), time(6, 0)))
    db.refresh(user)
    assert user.sentence_cursor == day1 + 1  # 다음 날 한 번만 넘어감


def test_test_push_endpoint(db, user, pusher):
    with TestClient(app) as c:
        login(c)
        r = c.post("/push/test").json()
        assert not r["ok"] and "기기가 없습니다" in r["message"]
        add_sub(db, user, "https://web.push.apple.com/me")
        r = c.post("/push/test").json()
        assert r["ok"] and "1대" in r["message"]
        sent = json.loads(pusher["calls"][-1]["data"])
        assert sent["title"] == T.PUSH_TITLE  # 테스트 알림 = 첫 알림과 같은 형식(과제 요약)
        assert sent["body"] == T.DAILY_PUSH_EMPTY or sent["body"].startswith("오늘의 공부 과제 ")


def test_generated_vapid_keys_sign():
    from py_vapid import Vapid

    pub, priv = generate()
    v = Vapid.from_string(priv)
    headers = v.sign({"sub": "mailto:a@b.c", "aud": "https://web.push.apple.com", "exp": 9999999999})
    assert pub in headers["Authorization"]  # vapid t=…, k=<공개키>
