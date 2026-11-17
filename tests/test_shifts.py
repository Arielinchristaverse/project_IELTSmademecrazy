"""사용자 정의 근무 유형: 추가·수정·보관·삭제, 격리, 8개 제한, 쉬는 날 기준 보충, 내일부터 재계산, 마이그레이션."""
import sqlite3
from datetime import date, datetime, time, timedelta

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import clock
from app.main import app
from app.models import Assignment, CalendarDay, ShiftType, Task
from app.services import shifts as svc
from app.services.allocator import generate_week
from app.services.daily import notify_due, prepare_day
from app.services.users import get_or_create_user
from scripts import migrate
from tests.conftest import login

MON = date(2026, 11, 2)


def shift(db, user, code):
    return db.scalar(select(ShiftType).where(ShiftType.user_id == user.id, ShiftType.code == code))


def form(**kw):
    base = {"label": "단축근무", "study_minutes": "90", "send_time": "14:00"}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


@pytest.fixture()
def on_day(monkeypatch):
    box = {"d": MON}
    monkeypatch.setattr(clock, "today", lambda: box["d"])
    monkeypatch.setattr(clock, "now", lambda: datetime.combine(box["d"], time(8, 0)))
    return box


# ------------------------------------------------------------------ 프리셋
def test_new_user_gets_four_presets(db, user):
    rows = svc.list_shifts(db, user.id)
    assert [s.label for s in rows] == ["데이", "이브닝", "나이트", "오프"]
    assert [s.is_rest_day for s in rows] == [False, False, False, True]
    assert [s.study_minutes for s in rows] == [60, 120, 60, 180]
    assert not hasattr(rows[0], "start_time")  # 근무 시각은 다루지 않음


# ------------------------------------------------------------------ 추가·수정·보관·삭제 (HTTP)
def test_add_edit_archive_unarchive_delete(db, user):
    with TestClient(app) as c:
        login(c)
        r = c.post("/settings/shifts/new", data=form(color="rose", is_rest_day="on"), follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/settings?msg=")
        s = db.scalar(select(ShiftType).where(ShiftType.user_id == user.id, ShiftType.label == "단축근무"))
        assert (s.code, s.study_minutes, s.send_time, s.is_rest_day, s.color) == ("U1", 90, time(14, 0), True, "rose")
        assert s.position == 4  # 프리셋 뒤

        html = c.get("/settings").text
        table = html.split('class="list shift-table"')[1].split("</table>")[0]
        assert table.count("<tr>") == 1 + 5  # 머리글 + 유형 5개 (한 유형 = 한 줄)
        row = next(r for r in table.split("<tr>") if "단축근무" in r)
        assert "90분" in row and "14:00" in row and "쉬는 날" in row and "편집" in row

        r = c.post(f"/settings/shifts/{s.id}", data=form(label="단축", study_minutes="30", is_rest_day=None))
        db.refresh(s)
        assert (s.label, s.study_minutes, s.is_rest_day) == ("단축", 30, False)

        c.post(f"/settings/shifts/{s.id}/archive", data={"back": "/settings"})
        db.refresh(s)
        assert s.is_archived
        assert "단축</option>" not in c.get("/calendar?month=2026-11").text  # 선택지에서 숨김

        c.post(f"/settings/shifts/{s.id}/unarchive", data={"back": "/settings"})
        db.refresh(s)
        assert not s.is_archived

        sid = s.id
        c.post(f"/settings/shifts/{sid}/delete", data={"back": "/settings"})  # 달력에서 안 썼으므로 삭제 가능
        db.expire_all()
        assert db.get(ShiftType, sid) is None


@pytest.mark.parametrize(
    "bad, msg",
    [
        (form(label=""), "이름을 입력"),
        (form(label="가" * 21), "20자 이하"),
        (form(label="데이"), "이미 있습니다"),
        (form(label=" 데이 "), "이미 있습니다"),
        (form(study_minutes="1500"), "0~1440"),
        (form(study_minutes="abc"), "숫자"),
        (form(send_time=""), "알림 시각"),
        (form(send_time="25:99"), "형식"),
        (form(color="gold"), "색상"),
    ],
)
def test_validation(db, user, bad, msg):
    with TestClient(app) as c:
        login(c)
        r = c.post("/settings/shifts/new", data=bad)
        assert r.status_code == 400 and msg in r.text
    assert len(svc.list_shifts(db, user.id)) == 4


def test_duplicate_name_includes_archived(db, user):
    off = shift(db, user, "OFF")
    svc.archive_shift(db, off)
    with pytest.raises(svc.ShiftError, match="보관된 유형"):
        svc.create_shift(db, user.id, svc.parse_form(form(label="오프")))


def test_max_eight_active_and_archived_not_counted(db, user):
    for i in range(4):
        svc.create_shift(db, user.id, svc.parse_form(form(label=f"추가{i}")))
    assert svc.active_count(db, user.id) == 8
    with pytest.raises(svc.ShiftError, match="8개"):
        svc.create_shift(db, user.id, svc.parse_form(form(label="아홉")))
    svc.archive_shift(db, shift(db, user, "U1"))
    svc.create_shift(db, user.id, svc.parse_form(form(label="아홉")))  # 보관은 개수에 안 들어감
    with pytest.raises(svc.ShiftError, match="8개"):
        svc.unarchive_shift(db, shift(db, user, "U1"))
    with TestClient(app) as c:
        login(c)
        assert "8개까지" in c.get("/settings/shifts/new").text


# ------------------------------------------------------------------ 사용 중 삭제 거부
def test_used_shift_cannot_be_deleted_only_archived(db, user):
    db.add(CalendarDay(user_id=user.id, date=MON, shift_code="NIGHT"))
    db.commit()
    night = shift(db, user, "NIGHT")
    with TestClient(app) as c:
        login(c)
        assert "삭제할 수 없어요" in c.get(f"/settings/shifts/{night.id}").text
        r = c.post(f"/settings/shifts/{night.id}/delete", data={"back": "/settings"})
        assert "삭제할 수 없어요" in r.text
        c.post(f"/settings/shifts/{night.id}/archive", data={"back": "/settings"})
        cal = c.get("/calendar?month=2026-11").text
        assert "나이트 (보관됨)</option>" in cal  # 이미 들어간 날은 유지
    db.expire_all()
    assert db.get(ShiftType, night.id).is_archived
    assert db.scalar(select(CalendarDay).where(CalendarDay.user_id == user.id, CalendarDay.date == MON)).shift_code == "NIGHT"


def test_database_foreign_key_also_blocks_deleting_used_shift(db, user):
    db.add(CalendarDay(user_id=user.id, date=MON, shift_code="DAY"))
    db.commit()
    db.delete(shift(db, user, "DAY"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_calendar_cannot_newly_pick_archived_or_foreign_shift(db, user):
    other = get_or_create_user(db, "other@example.com")
    other.onboarding_step = 3
    svc.create_shift(db, other.id, svc.parse_form(form(label="남의유형")))  # 다른 사용자 U1
    off = shift(db, user, "OFF")
    svc.archive_shift(db, off)
    with TestClient(app) as c:
        login(c)
        c.post("/calendar", data={"month": "2026-11", "shift_2026-11-02": "U1", "shift_2026-11-03": "OFF", "shift_2026-11-04": "DAY"})
    days = {c.date: c.shift_code for c in db.scalars(select(CalendarDay).where(CalendarDay.user_id == user.id))}
    assert MON not in days or days[MON] is None  # 나에게 없는 코드
    assert days.get(MON + timedelta(1)) is None  # 보관된 유형은 새로 고를 수 없음
    assert days[MON + timedelta(2)] == "DAY"


# ------------------------------------------------------------------ 격리
def test_other_users_shift_is_404(db, user):
    other = get_or_create_user(db, "other@example.com")
    theirs = svc.create_shift(db, other.id, svc.parse_form(form(label="그쪽유형")))
    with TestClient(app) as c:
        login(c)
        assert c.get(f"/settings/shifts/{theirs.id}").status_code == 404
        assert c.post(f"/settings/shifts/{theirs.id}", data=form(label="해킹")).status_code == 404
        for action in ("archive", "unarchive", "delete"):
            assert c.post(f"/settings/shifts/{theirs.id}/{action}", data={}).status_code == 404
        assert "그쪽유형" not in c.get("/settings").text
    db.refresh(theirs)
    assert theirs.label == "그쪽유형" and not theirs.is_archived


# ------------------------------------------------------------------ 직접 만든 유형의 배정·알림
def test_custom_shift_drives_capacity_and_notify_time_on_its_date(db, user):
    mine = svc.create_shift(db, user.id, svc.parse_form(form(label="교육", study_minutes="45", send_time="16:00")))
    db.add(CalendarDay(user_id=user.id, date=MON, shift_code=mine.code))
    db.add(Task(user_id=user.id, name="단어", subject="V", duration_min=30, weekly_count=3))
    user.notify_channel = "email"
    db.commit()
    generate_week(db, user.id, MON, MON)
    mon = db.scalars(select(Assignment).where(Assignment.user_id == user.id, Assignment.date == MON)).all()
    tue = db.scalars(select(Assignment).where(Assignment.user_id == user.id, Assignment.date == MON + timedelta(1))).all()
    assert mon and sum(a.task.duration_min for a in mon) <= 45  # 그 유형의 공부 시간 안에서
    assert tue == []  # 달력에 입력한 날에만 배정
    assert notify_due(db, user, datetime.combine(MON, time(15, 59))) == []
    assert notify_due(db, user, datetime.combine(MON, time(16, 0))) != []  # 그 유형의 알림 시각
    assert notify_due(db, user, datetime.combine(MON + timedelta(1), time(16, 0))) == []


# ------------------------------------------------------------------ 쉬는 날 기준 보충 (요일 무관)
def _week_with(db, user, wed_code):
    codes = ["DAY", "DAY", wed_code, "DAY", "DAY", "DAY", "DAY"]
    for i, code in enumerate(codes):
        db.add(CalendarDay(user_id=user.id, date=MON + timedelta(i), shift_code=code))
    db.add(Task(user_id=user.id, name="리딩", subject="R", duration_min=30, weekly_count=3))
    db.commit()
    generate_week(db, user.id, MON, MON)
    a = db.scalar(select(Assignment).where(Assignment.user_id == user.id, Assignment.date == MON))
    a.status = "missed"
    db.commit()
    return a


def makeups_on(db, user, d):
    return [a for a in db.scalars(select(Assignment).where(Assignment.user_id == user.id, Assignment.date == d)) if a.kind == "makeup"]


def test_custom_rest_day_gets_makeup_first(db, user):
    rest = svc.create_shift(db, user.id, svc.parse_form(form(label="휴무", is_rest_day="on", study_minutes="120")))
    missed = _week_with(db, user, rest.code)
    wed = MON + timedelta(2)
    prepare_day(db, user, wed)
    m = makeups_on(db, user, wed)
    assert m and m[0].makeup_of_id == missed.id


def test_off_without_rest_flag_is_a_weekday(db, user):
    off = shift(db, user, "OFF")
    svc.update_shift(db, off, svc.parse_form(form(label="오프", study_minutes="180", send_time="08:00")))
    assert not off.is_rest_day
    missed = _week_with(db, user, "OFF")
    wed, sun = MON + timedelta(2), MON + timedelta(6)
    prepare_day(db, user, wed)
    assert makeups_on(db, user, wed) == []  # 쉬는 날이 아니면 보충 안 함
    prepare_day(db, user, sun)
    assert makeups_on(db, user, sun) == []  # 일요일도 다른 요일과 같다: 쉬는 날 유형이 아니면 보충 안 함
    from app.services.allocator import pending_items

    assert missed.id in [p.id for p in pending_items(db, user.id)]  # 보류는 다음 쉬는 날까지 남는다


# ------------------------------------------------------------------ 수정 시 내일부터 재계산
def test_edit_recalculates_from_tomorrow_only(db, user, on_day):
    for i in range(7):
        db.add(CalendarDay(user_id=user.id, date=MON + timedelta(i), shift_code="EVENING"))
    db.add_all([
        Task(user_id=user.id, name="A", subject="a", duration_min=60, weekly_count=7),
        Task(user_id=user.id, name="B", subject="b", duration_min=30, weekly_count=7),
    ])
    db.commit()
    generate_week(db, user.id, MON, MON)
    mon_a = db.scalar(select(Assignment).where(Assignment.user_id == user.id, Assignment.date == MON))
    mon_a.status = "done"
    db.commit()

    on_day["d"] = MON + timedelta(1)  # 오늘 = 화요일

    def snapshot(d):
        return sorted(a.id for a in db.scalars(select(Assignment).where(Assignment.user_id == user.id, Assignment.date == d)))

    before_mon, before_tue = snapshot(MON), snapshot(MON + timedelta(1))
    evening = shift(db, user, "EVENING")
    recalculated = svc.update_shift(
        db, evening, svc.parse_form(form(label="이브닝", study_minutes="30", send_time="09:00"))
    )
    assert recalculated
    db.expire_all()
    assert snapshot(MON) == before_mon and db.get(Assignment, mon_a.id).status == "done"  # 지난 날·기록 유지
    assert snapshot(MON + timedelta(1)) == before_tue  # 오늘은 그대로 (이미 받은 알림과 일치)
    for i in range(2, 7):  # 내일부터는 새 공부 시간(30분)에 맞게
        d = MON + timedelta(i)
        assert sum(a.task.duration_min for a in db.scalars(select(Assignment).where(Assignment.user_id == user.id, Assignment.date == d))) <= 30

    # 이름·색만 바꾸면 재계산하지 않음
    assert not svc.update_shift(
        db, evening, svc.parse_form(form(label="저녁", study_minutes="30", send_time="10:00", color="sky"))
    )


# ------------------------------------------------------------------ 마이그레이션 0003 → 0004
def make_0003_db(path):
    url = f"sqlite:///{path}"
    command.upgrade(migrate.alembic_config(url), "0003")
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO users (id, email, google_sub, name, timezone, notify_channel, notify_email, sentence_cursor, "
        "onboarding_step, last_prepared_date, is_admin, created_at, last_login_at) "
        "VALUES (1, 'me@example.com', 'g', '', 'Asia/Seoul', 'both', '', 3, 3, NULL, 1, '2026-11-01 00:00:00.000000', NULL)"
    )
    for i, (code, label, minutes) in enumerate([("DAY", "데이", 60), ("EVENING", "이브닝", 120), ("NIGHT", "나이트", 60), ("OFF", "오프", 180)]):
        con.execute(
            "INSERT INTO shift_types (id, user_id, code, label, study_minutes, send_time) VALUES (?, 1, ?, ?, ?, '07:30:00.000000')",
            (i + 1, code, label, minutes),
        )
    for i, code in enumerate(["DAY", "NIGHT", "OFF", "EVENING", "LEGACY"]):  # LEGACY = 유형 표에 없는 코드
        con.execute("INSERT INTO calendar_days (user_id, date, shift_code, excluded) VALUES (1, ?, ?, 0)", (f"2026-11-0{i + 2}", code))
    con.execute("INSERT INTO tasks (id, user_id, name, subject, duration_min, weekly_count, active) VALUES (5, 1, 'R', 'r', 30, 3, 1)")
    con.execute(
        "INSERT INTO assignments (id, user_id, week_start, date, task_id, kind, status, makeup_of_id, position, recorded_at) "
        "VALUES (9, 1, '2026-11-02', '2026-11-02', 5, 'regular', 'done', NULL, 0, NULL)"
    )
    con.commit()
    con.close()
    return url


def test_migration_0004_preserves_data(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)  # 0002 가 관리자 계정을 따로 만들지 않도록
    path = tmp_path / "app.db"
    url = make_0003_db(path)
    result = migrate.run(url)
    assert result.startswith("upgraded 0003 ->") and "backup" in result

    con = sqlite3.connect(path)
    rows = {r[0]: r[1:] for r in con.execute(
        "SELECT code, label, study_minutes, is_rest_day, position, archived_at IS NOT NULL, color "
        "FROM shift_types WHERE user_id = 1"
    )}
    assert rows["DAY"] == ("데이", 60, 0, 0, 0, "sky")
    assert rows["NIGHT"][:4] == ("나이트", 60, 0, 2)
    cols = [r[1] for r in con.execute("PRAGMA table_info(shift_types)")]
    assert "start_time" not in cols and "end_time" not in cols
    assert rows["OFF"][:3] == ("오프", 180, 1)  # OFF → 쉬는 날
    assert rows["LEGACY"][4] == 1 and rows["LEGACY"][0] == "LEGACY"  # 고아 코드는 보관된 유형으로 보존
    assert [r[0] for r in con.execute("SELECT shift_code FROM calendar_days ORDER BY date")] == ["DAY", "NIGHT", "OFF", "EVENING", "LEGACY"]
    assert con.execute("SELECT status FROM assignments WHERE id = 9").fetchone()[0] == "done"
    fks = con.execute("PRAGMA foreign_key_list(calendar_days)").fetchall()
    assert any(f[2] == "shift_types" and f[3] == "user_id" for f in fks) and any(f[3] == "shift_code" and f[4] == "code" for f in fks)
    con.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM shift_types WHERE code = 'NIGHT'")  # 사용 중 유형 삭제는 DB가 거부
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO shift_types (user_id, code, label, study_minutes, send_time, is_rest_day, position) VALUES (1, 'X', '데이', 0, '07:00:00', 0, 0)")
    con.close()
    assert len(list((tmp_path / "backups").iterdir())) == 1


def test_migration_0004_failure_rolls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    path = tmp_path / "app.db"
    url = make_0003_db(path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE _alembic_tmp_calendar_days (x INTEGER)")  # 5단계(달력 표 재생성)에서 일부러 실패하게
    con.commit()
    con.close()
    with pytest.raises(Exception):
        migrate.run(url)
    con = sqlite3.connect(path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(shift_types)")]
    assert "is_rest_day" not in cols  # 1단계에서 추가한 칸까지 모두 되돌려짐
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0003"
    assert con.execute("SELECT count(*) FROM calendar_days").fetchone()[0] == 5
    con.close()
