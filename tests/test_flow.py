from datetime import date, datetime, time, timedelta

from fastapi.testclient import TestClient

from tests.conftest import login
from sqlalchemy import select

from app.models import ActionToken, Assignment, CalendarDay, NotifyLog, Task
from app.services.allocator import generate_week, pending_items
from app.services.daily import notify_due, prepare_day, send_test_mail
from app.services.progress import week_progress

MON = date(2026, 11, 2)


def setup_month(db, user, shifts):
    for i, code in enumerate(shifts):
        db.add(CalendarDay(user_id=user.id, date=MON + timedelta(days=i), shift_code=code))
    db.add_all([
        Task(user_id=user.id, name="Reading", subject="R", duration_min=60, weekly_count=3),
        Task(user_id=user.id, name="Vocab", subject="V", duration_min=30, weekly_count=5),
    ])
    db.commit()


def day_items(db, d, user=None):
    q = select(Assignment).where(Assignment.date == d)
    if user is not None:
        q = q.where(Assignment.user_id == user.id)
    return db.scalars(q).all()


def test_generate_and_auto_miss_and_weekend_makeup(db, user):
    # 월~토 데이(60분), 수요일은 오프(180분), 일요일 오프
    setup_month(db, user, ["DAY", "DAY", "OFF", "DAY", "DAY", "DAY", "OFF"])
    generate_week(db, user.id, MON, MON)
    mon_items = day_items(db, MON)
    assert mon_items and sum(a.task.duration_min for a in mon_items) <= 60

    # 월요일 기록 안 함 → 화요일 메일 준비 시 자동 미달성
    prepare_day(db, user, MON + timedelta(days=1))
    assert all(a.status == "missed" for a in day_items(db, MON))
    assert len(pending_items(db, user.id)) == len(mon_items)

    # 수요일은 오프 → 보충 과제가 먼저 배정
    wed = MON + timedelta(days=2)
    prepare_day(db, user, wed)
    wed_items = sorted(day_items(db, wed), key=lambda a: a.position)
    assert wed_items[0].kind == "makeup"
    assert pending_items(db, user.id) == []


def test_mail_buttons_record_once(db, user):
    setup_month(db, user, ["DAY"] * 5 + ["OFF", "OFF"])
    client = TestClient(__import__("app.main", fromlist=["app"]).app)
    send_test_mail(db, user, MON)
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
    assert week_progress(db, user.id, MON) > 0
    assert client.get("/a/nope").status_code == 404


def test_makeup_done_marks_original_done(db, user):
    setup_month(db, user, ["DAY"] * 5 + ["DAY", "OFF"])
    generate_week(db, user.id, MON, MON)
    original = day_items(db, MON)[0]
    original.status = "missed"
    db.commit()
    sun = MON + timedelta(days=6)
    prepare_day(db, user, sun)
    makeup = next(a for a in day_items(db, sun) if a.kind == "makeup")
    from app.services.progress import set_status

    before = week_progress(db, user.id, MON)
    set_status(db, makeup, "done")
    db.refresh(original)
    assert original.status == "done"
    assert week_progress(db, user.id, MON) > before


def test_scheduler_sends_once_after_send_time(db, user):
    setup_month(db, user, ["DAY"] * 7)
    t = datetime.combine(MON, time(5, 0))
    assert notify_due(db, user, t) == []  # 발송 시각(05:30) 전
    assert notify_due(db, user, t.replace(hour=6)) != []
    assert notify_due(db, user, t.replace(hour=7)) == []  # 하루 한 번
    assert db.scalar(select(NotifyLog)).status == "sent"


def test_excluded_day_no_mail(db, user):
    setup_month(db, user, ["DAY"] * 7)
    db.scalar(select(CalendarDay).where(CalendarDay.user_id == user.id, CalendarDay.date == MON)).excluded = True
    db.commit()
    assert notify_due(db, user, datetime.combine(MON, time(9, 0))) == []


def test_login_required(db, user):
    from app.main import app

    with TestClient(app) as c:
        assert c.get("/", follow_redirects=False).status_code == 303
        login(c)
        for path in ["/", "/calendar", "/tasks", "/settings", "/sentences"]:
            assert c.get(path).status_code == 200, path


def test_future_week_does_not_take_pending(db, user):
    setup_month(db, user, ["DAY", "DAY", "DAY", "DAY", "DAY", "OFF", "OFF"] * 2)
    generate_week(db, user.id, MON, MON)
    a = day_items(db, MON)[0]
    a.status = "missed"
    db.commit()
    generate_week(db, user.id, MON + timedelta(days=7), MON)  # 다음 주를 미리 생성
    assert len(pending_items(db, user.id)) == 1  # 이번 주 주말을 위해 남아 있어야 함
    sat = MON + timedelta(days=5)
    prepare_day(db, user, sat)  # 이번 주 토요일(오프): 가장 오래된 보류가 먼저 보충됨
    makeups = [x for x in day_items(db, sat) if x.kind == "makeup"]
    assert a.id in {m.makeup_of_id for m in makeups}
    next_sat = [x for x in day_items(db, sat + timedelta(days=7)) if x.kind == "makeup"]
    assert next_sat == []


def test_mail_preview_page(db, user):
    from app.main import app

    setup_month(db, user, ["DAY"] * 7)
    with TestClient(app) as c:
        login(c)
        c.post("/mail/test", data={"day": MON.isoformat()})
        page = c.get(f"/?start={MON}").text
        assert "/mail/preview/" in page
        name = page.split("/mail/preview/")[1].split('"')[0]
        r = c.get(f"/mail/preview/{name}")
        assert r.status_code == 200 and "오늘의 과제" in r.text
        assert c.get("/mail/preview/..%2F.env").status_code == 404


def test_codespaces_base_url(monkeypatch):
    from app.config import _default_base_url

    monkeypatch.setenv("CODESPACE_NAME", "cool-name")
    monkeypatch.setenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    assert _default_base_url() == "https://cool-name-8000.app.github.dev"


def test_production_refuses_dev_login(monkeypatch):
    """운영(Render)에서 개발용 로그인이 켜져 있으면 서버가 시작을 거부한다."""
    from app.config import settings

    monkeypatch.setattr(type(settings), "is_production", True)
    monkeypatch.setattr(type(settings), "dev_login", True)
    monkeypatch.setattr(type(settings), "secret_key", "real-secret")
    for k in ("vapid_public_key", "vapid_private_key", "vapid_subject"):
        monkeypatch.setattr(type(settings), k, "x")
    assert any("DEV_LOGIN" in p for p in settings.production_problems())
    monkeypatch.setattr(type(settings), "dev_login", False)
    assert settings.production_problems() == []


def test_healthz_is_public():
    from app.main import app

    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"ok": True}


def test_render_base_url(monkeypatch):
    from app.config import _default_base_url

    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://study-shift.onrender.com")
    assert _default_base_url() == "https://study-shift.onrender.com"


def test_backup_download_is_valid_sqlite(db, user, tmp_path):
    import sqlite3

    from app.main import app

    setup_month(db, user, ["DAY"] * 7)
    with TestClient(app) as c:
        assert c.get("/admin/backup", follow_redirects=False).status_code == 303  # 로그인 필요
        login(c)
        r = c.get("/admin/backup")
        assert r.status_code == 200
        assert "attachment" in r.headers["content-disposition"]
    f = tmp_path / "b.db"
    f.write_bytes(r.content)
    con = sqlite3.connect(f)
    assert con.execute("select count(*) from tasks").fetchone()[0] == 2
    con.close()


def test_production_requires_vapid_when_push_enabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(type(settings), "is_production", True)
    monkeypatch.setattr(type(settings), "secret_key", "real-secret")
    monkeypatch.setattr(type(settings), "dev_login", False)
    monkeypatch.setattr(type(settings), "vapid_private_key", "")
    assert any("VAPID" in p for p in settings.production_problems())
    monkeypatch.setattr(type(settings), "notify_channels", ("email",))
    assert settings.production_problems() == []
