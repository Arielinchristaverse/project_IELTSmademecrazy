"""'오늘' 화면: 메일과 같은 내용 · 로그인 상태에서 바로 달성/미달성 · 주말 재배정 하루 1회."""
import os
from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.main import app
from app.models import Assignment, CalendarDay, Task
from app.services.daily import send_test_mail
from tests.conftest import login

MON = date(2026, 11, 2)


@pytest.fixture()
def on_day(monkeypatch):
    """오늘 날짜를 바꿔 끼운다."""
    box = {"d": MON}
    monkeypatch.setattr(clock, "today", lambda: box["d"])
    monkeypatch.setattr(clock, "now", lambda: datetime.combine(box["d"], time(8, 0)))
    return box


def progress_of(html: str) -> int:
    return int(html.split('class="stat">')[1].split("<")[0])


def setup(db, user, shifts=("DAY",) * 6 + ("OFF",)):
    for i, code in enumerate(shifts):
        db.add(CalendarDay(user_id=user.id, date=MON + timedelta(days=i), shift_code=code))
    db.add_all([
        Task(user_id=user.id, name="Reading", subject="R", duration_min=60, weekly_count=3),
        Task(user_id=user.id, name="Vocab", subject="V", duration_min=30, weekly_count=5),
    ])
    db.commit()


def test_today_shows_mail_content_and_records_directly(db, user, on_day):
    setup(db, user)
    with TestClient(app) as c:
        login(c)
        html = c.get("/today").text
        assert "Today · 11.02 MON" in html and "11월 2일(월)" in html and "데이 근무일" in html
        assert "이번 주 진척도" in html and progress_of(html) == 0
        assert "주말 보충 리스트" in html and "보류된 과제가 없습니다" in html
        assert "There is" in html or "“" in html  # 영어 문장
        todays = db.scalars(select(Assignment).where(Assignment.user_id == user.id, Assignment.date == MON)).all()
        assert todays and all(f"/assignments/{a.id}/status" in html for a in todays)

        done, missed = todays[0], todays[-1] if len(todays) > 1 else None
        r = c.post(f"/assignments/{done.id}/status", data={"status": "done", "back": "/today"}, follow_redirects=False)
        assert r.headers["location"] == "/today"
        html = c.get("/today").text
        assert progress_of(html) > 0  # 진척도 반영
        assert 'aria-pressed="true"' in html  # 체크 상태

        if missed:
            c.post(f"/assignments/{missed.id}/status", data={"status": "missed", "back": "/today"})
            html = c.get("/today").text
            assert "미달성 · 주말 보충" in html
            pending_part = html.split("주말 보충 리스트")[1]
            assert missed.task.name in pending_part  # 보류 목록으로 이동

            c.post(f"/assignments/{missed.id}/status", data={"status": "scheduled", "back": "/today"})  # 되돌리기
            db.expire_all()
            assert db.get(Assignment, missed.id).status == "scheduled"


def test_same_sentence_in_mail_and_today_and_next_day_changes(db, user, on_day):
    setup(db, user)
    with TestClient(app) as c:
        login(c)
        page1 = c.get("/today").text
        send_test_mail(db, user, MON)
        mail = open(os.path.join("outbox", sorted(os.listdir("outbox"))[-1]), encoding="utf-8").read()
        quote = page1.split('class="sentence">')[1].split("</p>")[0].split("“")[1].split("”")[0]
        assert quote and quote in mail
        assert quote in c.get("/today").text  # 같은 날 다시 열어도 같은 문장

        on_day["d"] = MON + timedelta(days=1)
        page2 = c.get("/today").text
        assert quote not in page2.split('class="sentence">')[1]


def test_weekend_reallocation_happens_once_per_day(db, user, on_day):
    setup(db, user)
    sun = MON + timedelta(days=6)
    with TestClient(app) as c:
        login(c)
        on_day["d"] = sun
        c.get("/today")
        first = [a.id for a in db.scalars(select(Assignment).where(Assignment.user_id == user.id, Assignment.date == sun))]
        c.get("/today")
        c.get("/today")
        db.expire_all()
        again = [a.id for a in db.scalars(select(Assignment).where(Assignment.user_id == user.id, Assignment.date == sun))]
        assert first == again  # 열 때마다 다시 배정되지 않음


def test_today_empty_states(db, user, on_day):
    with TestClient(app) as c:
        login(c)
        assert "근무가 입력되지 않았어요" in c.get("/today").text
        db.add(CalendarDay(user_id=user.id, date=MON, shift_code="DAY", excluded=True))
        db.commit()
        assert "공부 제외일" in c.get("/today").text


def test_today_requires_login():
    with TestClient(app) as c:
        assert c.get("/today", follow_redirects=False).headers["location"] == "/login"
