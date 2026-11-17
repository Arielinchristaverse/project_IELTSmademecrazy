"""미달성(미기록) 과제 재알림."""
import json
import os
import sqlite3
from datetime import date, datetime, time, timedelta

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.main import app
from app.models import Assignment, CalendarDay, NotifyLog, PushSubscription, ShiftType, Task
from app.services import notify_text as T
from app.services import shifts as svc
from app.services.daily import notify_due, remind_due, run_due
from app.services.users import get_or_create_user
from scripts import migrate
from tests.conftest import login
from tests.test_push import add_sub, pusher  # noqa: F401  (pusher 는 fixture)

MON = date(2026, 11, 2)


def at(hh, mm=0, d=MON):
    return datetime.combine(d, time(hh, mm))


def setup(db, user, send=time(6, 0), remind=time(12, 0), channel="push", endpoint=None, tasks=3):
    day = db.scalar(select(ShiftType).where(ShiftType.user_id == user.id, ShiftType.code == "DAY"))
    day.send_time, day.remind_time = send, remind
    db.add(CalendarDay(user_id=user.id, date=MON, shift_code="DAY"))
    for i in range(tasks):
        db.add(Task(user_id=user.id, name=f"과제{i}", subject=f"s{i}", duration_min=15, weekly_count=7))
    user.notify_channel = channel
    user.onboarding_step = 3
    db.commit()
    if channel in ("push", "both"):
        add_sub(db, user, endpoint or f"https://web.push.apple.com/{user.id}")
    return day


def todays(db, user):
    return db.scalars(select(Assignment).where(Assignment.user_id == user.id, Assignment.date == MON)).all()


def remind_calls(box):
    return [c for c in box["calls"] if json.loads(c["data"])["tag"].startswith("remind-")]


def remind_logs(db, user):
    return db.scalars(select(NotifyLog).where(NotifyLog.user_id == user.id, NotifyLog.kind == "remind")).all()


# ------------------------------------------------------------------ 1. 미기록 과제가 있을 때만
def test_remind_only_when_unrecorded_tasks_remain(db, user, pusher):
    setup(db, user)
    assert notify_due(db, user, at(6))  # 정기 알림
    tasks = todays(db, user)
    assert len(tasks) >= 2
    tasks[0].status = "done"
    db.commit()
    assert remind_due(db, user, at(11, 59)) == []  # 재알림 시각 전
    out = remind_due(db, user, at(12, 0))
    assert out and out[0].startswith("push(재알림)")
    data = json.loads(remind_calls(pusher)[0]["data"])
    assert data["body"] == T.REMIND_PUSH_BODY.format(count=len(tasks) - 1) == f"오늘 남은 과제 {len(tasks) - 1}개"
    assert data["url"] == "/today" and data["tag"] == f"remind-{MON.isoformat()}"
    assert len(remind_calls(pusher)[0]["data"].encode()) < 200


# ------------------------------------------------------------------ 2. 전부 기록하면 미발송
def test_no_remind_when_everything_recorded(db, user, pusher):
    setup(db, user)
    notify_due(db, user, at(6))
    for i, a in enumerate(todays(db, user)):
        a.status = "done" if i % 2 == 0 else "missed"
    db.commit()
    assert remind_due(db, user, at(12)) == [] and remind_calls(pusher) == []


# ------------------------------------------------------------------ 3. '미달성'만 남은 경우 미발송
def test_no_remind_when_only_missed_tasks_left(db, user, pusher):
    setup(db, user)
    notify_due(db, user, at(6))
    for a in todays(db, user):
        a.status = "missed"
    db.commit()
    assert remind_due(db, user, at(12)) == []


# ------------------------------------------------------------------ 4. 하루 1회
def test_remind_once_per_day(db, user, pusher):
    setup(db, user)
    notify_due(db, user, at(6))
    for t in (at(12), at(12, 1), at(15), at(23, 59)):
        remind_due(db, user, t)
        run_due(db, t)
    assert len(remind_calls(pusher)) == 1
    assert [(log.kind, log.channel, log.status) for log in remind_logs(db, user)] == [("remind", "push", "sent")]


# ------------------------------------------------------------------ 5. 재알림 시각 비움
def test_no_remind_when_remind_time_empty(db, user, pusher):
    setup(db, user, remind=None)
    notify_due(db, user, at(6))
    assert remind_due(db, user, at(23, 59)) == [] and remind_calls(pusher) == []


# ------------------------------------------------------------------ 6. 사용자별 시각
def test_each_user_reminded_at_own_time(db, user, pusher):
    b = get_or_create_user(db, "b@example.com")
    setup(db, user, send=time(6), remind=time(12))
    setup(db, b, send=time(14), remind=time(20))

    def reminded():
        return [c["endpoint"].rsplit("/", 1)[1] for c in remind_calls(pusher)]

    for t in (at(6), at(11, 59)):
        run_due(db, t)
    assert reminded() == []
    run_due(db, at(12))
    assert reminded() == [str(user.id)]
    run_due(db, at(14))  # B 정기 알림
    run_due(db, at(19, 59))
    assert reminded() == [str(user.id)]
    run_due(db, at(20))
    assert reminded() == [str(user.id), str(b.id)]


# ------------------------------------------------------------------ 7. 서버 재시작 후 같은 날 늦게
def test_late_send_after_restart_same_day(db, user, pusher):
    setup(db, user)
    run_due(db, at(23, 50))  # 하루 종일 꺼져 있다가 23:50 에 처음 확인
    tags = [json.loads(c["data"])["tag"] for c in pusher["calls"]]
    assert tags == [f"daily-{MON.isoformat()}", f"remind-{MON.isoformat()}"]  # 정기 → 재알림 순서로 늦게라도


# ------------------------------------------------------------------ 8. 자정 이후 미발송
def test_no_remind_after_midnight(db, user, pusher):
    setup(db, user)
    notify_due(db, user, at(6))
    tue = MON + timedelta(days=1)
    assert remind_due(db, user, at(0, 5, tue)) == []
    run_due(db, at(0, 5, tue))
    assert remind_calls(pusher) == [] and remind_logs(db, user) == []


# ------------------------------------------------------------------ 추가 확인
def test_no_remind_when_daily_not_sent(db, user, pusher):
    setup(db, user, endpoint="https://web.push.apple.com/flaky")
    pusher["fail"]["https://web.push.apple.com/flaky"] = 503
    notify_due(db, user, at(6))  # 정기 알림 실패
    assert remind_due(db, user, at(12)) == []
    assert remind_due(db, user, at(13)) == []
    assert db.scalar(select(NotifyLog).where(NotifyLog.kind == "daily")).status == "failed"


def test_no_remind_without_devices(db, user, pusher):
    setup(db, user, channel="push")
    db.execute(delete(PushSubscription))
    db.commit()
    notify_due(db, user, at(6))  # 기기가 없어 '건너뜀'
    assert remind_due(db, user, at(12)) == []


def test_remind_per_channel_follows_daily_success(db, user, pusher):
    """푸시 정기 알림은 실패, 메일은 성공 → 재알림은 메일로만."""
    setup(db, user, channel="both", endpoint="https://web.push.apple.com/down")
    pusher["fail"]["https://web.push.apple.com/down"] = 500
    notify_due(db, user, at(6))
    out = remind_due(db, user, at(12))
    assert [o.split("(")[0] for o in out] == ["email"]
    assert remind_calls(pusher) == []


@pytest.mark.parametrize("case", ["excluded", "no_shift", "zero_minutes", "no_assignments"])
def test_no_remind_on_days_without_study(db, user, pusher, case):
    day = setup(db, user, tasks=0 if case == "no_assignments" else 3)
    cal = db.scalar(select(CalendarDay).where(CalendarDay.user_id == user.id, CalendarDay.date == MON))
    if case == "excluded":
        cal.excluded = True
    elif case == "no_shift":
        cal.shift_code = None
    elif case == "zero_minutes":
        day.study_minutes = 0
    db.commit()
    db.expire_all()  # 스케줄러는 매 분 새 세션을 쓰므로, 테스트에서도 바뀐 값을 다시 읽게 한다
    run_due(db, at(23, 0))
    assert remind_calls(pusher) == [] and remind_logs(db, user) == []


def test_remind_retries_every_10_minutes_up_to_3(db, user, pusher):
    setup(db, user, endpoint="https://web.push.apple.com/x")
    notify_due(db, user, at(6))
    pusher["fail"]["https://web.push.apple.com/x"] = 503
    remind_due(db, user, at(12))
    assert remind_due(db, user, at(12, 5)) == []
    remind_due(db, user, at(12, 11))
    remind_due(db, user, at(12, 22))
    assert remind_due(db, user, at(12, 40)) == []
    log = remind_logs(db, user)[0]
    assert (log.status, log.attempts) == ("failed", 3)


def test_expired_subscription_removed_on_remind(db, user, pusher):
    setup(db, user, endpoint="https://web.push.apple.com/old")
    notify_due(db, user, at(6))
    pusher["fail"]["https://web.push.apple.com/old"] = 410
    remind_due(db, user, at(12))
    assert db.scalar(select(PushSubscription)) is None
    assert remind_logs(db, user)[0].status == "skipped"


def test_remind_mail_lists_only_unrecorded_tasks(db, user):
    setup(db, user, channel="email")
    notify_due(db, user, at(6))
    tasks = todays(db, user)
    tasks[0].status = "done"
    done_name = tasks[0].task.name
    db.commit()
    before = set(os.listdir("outbox"))
    remind_due(db, user, at(12))
    new = sorted(set(os.listdir("outbox")) - before)
    html = open(os.path.join("outbox", new[-1]), encoding="utf-8").read()
    assert T.REMIND_MAIL_HEADING in html
    task_section = html.split(T.REMIND_MAIL_HEADING)[1].split("이번 주 진척도")[0]
    assert done_name not in task_section
    assert all(a.task.name in task_section for a in tasks[1:])


def test_other_users_records_do_not_count(db, user, pusher):
    b = get_or_create_user(db, "b@example.com")
    setup(db, user)
    setup(db, b)
    run_due(db, at(6))
    for a in todays(db, b):
        a.status = "done"
    db.commit()
    run_due(db, at(12))
    assert [c["endpoint"].rsplit("/", 1)[1] for c in remind_calls(pusher)] == [str(user.id)]


# ------------------------------------------------------------------ 설정 화면 · 기본값
def test_default_remind_is_send_plus_6h_or_none():
    assert svc.default_remind(time(5, 30)) == time(11, 30)
    assert svc.default_remind(time(17, 59)) == time(23, 59)
    assert svc.default_remind(time(18, 0)) is None
    assert svc.default_remind(time(22, 0)) is None


def test_presets_have_default_remind(db, user):
    rows = {s.code: s.remind_time for s in svc.list_shifts(db, user.id)}
    assert rows == {"DAY": time(11, 30), "EVENING": time(15, 0), "NIGHT": time(21, 0), "OFF": time(14, 0)}


def test_settings_form_remind_on_off_and_validation(db, user):
    base = {"label": "교육", "study_minutes": "30", "send_time": "09:00"}
    with TestClient(app) as c:
        login(c)
        assert 'name="remind_time" value="13:00"' in c.get("/settings/shifts/new").text  # 새 유형 기본값 표시
        r = c.post("/settings/shifts/new", data={**base, "remind_on": "on", "remind_time": "08:00"})
        assert r.status_code == 400 and "알림 시각보다 늦어야" in r.text
        r = c.post("/settings/shifts/new", data={**base, "remind_on": "on", "remind_time": ""})
        assert r.status_code == 400 and "재알림 시각" in r.text
        c.post("/settings/shifts/new", data={**base, "remind_on": "on", "remind_time": "18:30"})
        s = db.scalar(select(ShiftType).where(ShiftType.user_id == user.id, ShiftType.label == "교육"))
        assert s.remind_time == time(18, 30)
        c.post(f"/settings/shifts/{s.id}", data={**base, "remind_time": "18:30"})  # 체크 해제 = 끄기
        db.refresh(s)
        assert s.remind_time is None
        html = c.get("/settings").text
        row = next(r for r in html.split("<tr>") if "교육" in r)
        assert "—" in row  # 재알림 칸


def test_quick_table_moves_remind_when_send_time_passes_it(db, user):
    day = db.scalar(select(ShiftType).where(ShiftType.user_id == user.id, ShiftType.code == "DAY"))
    assert day.remind_time == time(11, 30)
    svc.set_quick(db, day, 60, time(12, 0))  # 알림을 재알림 이후로 → 12:00 + 6h
    assert day.remind_time == time(18, 0)
    svc.set_quick(db, day, 60, time(19, 0))  # 19:00 + 6h 는 자정 초과 → 끔
    assert day.remind_time is None
    svc.set_quick(db, day, 60, time(6, 0))  # 꺼진 재알림은 그대로 꺼진 채
    assert day.remind_time is None


# ------------------------------------------------------------------ 마이그레이션 0004 → 0005
def make_0004_db(path):
    url = f"sqlite:///{path}"
    command.upgrade(migrate.alembic_config(url), "0004")
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO users (id, email, google_sub, name, timezone, notify_channel, notify_email, sentence_cursor, "
        "onboarding_step, last_prepared_date, is_admin, created_at, last_login_at) "
        "VALUES (1, 'me@example.com', 'g', '', 'Asia/Seoul', 'push', '', 0, 3, NULL, 1, '2026-11-01 00:00:00.000000', NULL)"
    )
    for i, (code, send) in enumerate([("DAY", "05:30"), ("EVENING", "09:00"), ("NIGHT", "15:00"), ("OFF", "20:00")]):
        con.execute(
            "INSERT INTO shift_types (user_id, code, label, study_minutes, send_time, is_rest_day, color, position, archived_at) "
            "VALUES (1, ?, ?, 60, ?, ?, NULL, ?, NULL)",
            (code, code.lower(), f"{send}:00.000000", int(code == "OFF"), i),
        )
    con.execute(
        "INSERT INTO notify_logs (user_id, date, channel, sent_at, status, attempts, error) "
        "VALUES (1, '2026-11-02', 'push', '2026-11-02 05:30:00.000000', 'sent', 1, '')"
    )
    con.commit()
    con.close()
    return url


def test_migration_0005_defaults_and_kind(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    path = tmp_path / "app.db"
    url = make_0004_db(path)
    assert migrate.run(url).startswith("upgraded 0004 ->")
    con = sqlite3.connect(path)
    rows = dict(con.execute("select code, remind_time from shift_types"))
    assert rows == {"DAY": "11:30:00.000000", "EVENING": "15:00:00.000000", "NIGHT": "21:00:00.000000", "OFF": None}
    assert con.execute("select kind, status from notify_logs").fetchall() == [("daily", "sent")]
    # 같은 날·같은 수단이라도 종류가 다르면 기록 가능, 같은 종류는 중복 불가
    con.execute(
        "insert into notify_logs (user_id, date, kind, channel, sent_at, status, attempts, error) "
        "values (1, '2026-11-02', 'remind', 'push', '2026-11-02 12:00:00', 'sent', 1, '')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "insert into notify_logs (user_id, date, kind, channel, sent_at, status, attempts, error) "
            "values (1, '2026-11-02', 'daily', 'push', '2026-11-02 12:00:00', 'sent', 1, '')"
        )
    con.close()
    assert len(list((tmp_path / "backups").iterdir())) == 1


def test_migration_0005_failure_rolls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    path = tmp_path / "app.db"
    url = make_0004_db(path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE _alembic_tmp_notify_logs (x INTEGER)")  # 두 번째 단계에서 일부러 실패
    con.commit()
    con.close()
    with pytest.raises(Exception):
        migrate.run(url)
    con = sqlite3.connect(path)
    assert "remind_time" not in [r[1] for r in con.execute("PRAGMA table_info(shift_types)")]  # 첫 단계도 되돌려짐
    assert con.execute("select version_num from alembic_version").fetchone()[0] == "0004"
    con.close()
