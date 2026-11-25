"""3단계: 선착순 정원제 가입 · 가입 동의 · 재동의 · 약관/방침 문서 · 0008."""
import sqlite3
import threading
from dataclasses import replace
from datetime import timedelta

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import clock
from app.config import settings
from app.main import app
from app.models import User
from app.services import legal
from app.services.users import CapacityFull, create_user, get_or_create_user, members_count, record_consent
from scripts import migrate
from tests.conftest import csrf, login

AGREE = {"agree_terms": "on", "agree_privacy": "on", "agree_age": "on"}


@pytest.fixture()
def cap(monkeypatch):
    def set_cap(n):
        monkeypatch.setattr(type(settings), "max_users", n)
    return set_cap


def member(db, email, consent=True):
    u = get_or_create_user(db, email)
    u.onboarding_step = 3
    if consent:
        record_consent(u)
    db.commit()
    return u


def signup(c, email):
    """새 사용자 가입 시도: 개발용 로그인(동의 없이) → 동의 화면 → 동의 제출."""
    r = c.post("/auth/dev", data={"email": email}, follow_redirects=False)
    if r.status_code != 303:
        return r
    assert r.headers["location"] == "/consent"
    return c.post("/consent", data={**AGREE, "csrf": csrf(c)}, follow_redirects=False)


def users(db):
    db.expire_all()
    return sorted(u.email for u in db.scalars(select(User)))


# ------------------------------------------------------------------ 정원
def test_signup_under_capacity(db, user, cap):
    cap(2)
    with TestClient(app) as c:
        r = signup(c, "a@example.com")
        assert r.status_code == 303 and r.headers["location"] == "/today"
    assert "a@example.com" in users(db)


def test_full_capacity_rejects_new_and_shows_notice(db, user, cap):
    cap(1)
    member(db, "a@example.com")
    with TestClient(app) as c:
        r = c.post("/auth/dev", data={"email": "b@example.com"})
        assert r.status_code == 403 and "지금은 정원이 가득 찼어요" in r.text
        assert "arielinafterwork@gmail.com" in r.text and "1 /" not in r.text
        login_html = c.get("/login").text
        assert "정원" not in login_html and "/ 1" not in login_html  # 로그인 화면에는 숫자 없음
    assert "b@example.com" not in users(db)


def test_full_while_on_consent_screen(db, user, cap):
    """동의 화면을 보는 사이 정원이 차면, 제출해도 만들지 않고 안내."""
    cap(1)
    with TestClient(app) as c:
        assert c.post("/auth/dev", data={"email": "b@example.com"}, follow_redirects=False).headers["location"] == "/consent"
        member(db, "a@example.com")  # 그 사이 다른 사람이 가입
        r = c.post("/consent", data={**AGREE, "csrf": csrf(c)})
        assert r.status_code == 403 and "정원이 가득" in r.text
    assert "b@example.com" not in users(db)


def test_existing_users_always_log_in_when_full(db, user, cap):
    cap(1)
    member(db, "a@example.com")
    member(db, "b@example.com")  # 정원을 넘어 있어도(예: 정원을 줄인 경우)
    with TestClient(app) as c:
        login(c, "b@example.com", agree=False)
        assert c.get("/", follow_redirects=False).status_code == 200


def test_admin_not_counted(db, cap, monkeypatch):
    cap(1)
    monkeypatch.setattr(type(settings), "admin_email", "boss@example.com")
    member(db, "a@example.com")
    with TestClient(app) as c:
        assert signup(c, "boss@example.com").headers["location"] == "/today"  # 정원이 차도 관리자는 가입
    boss = db.scalar(select(User).where(User.email == "boss@example.com"))
    assert boss.is_admin and members_count(db) == 1


def test_account_delete_returns_seat(db, user, cap):
    cap(1)
    a = member(db, "a@example.com")
    with TestClient(app) as c:
        assert c.post("/auth/dev", data={"email": "b@example.com"}).status_code == 403
        login(c, "a@example.com")
        c.post("/account/delete", data={"confirm_email": "a@example.com", "csrf": csrf(c, "/account")})
        assert db.get(User, a.id) is None or users(db).count("a@example.com") == 0
        assert signup(c, "b@example.com").headers["location"] == "/today"


def test_concurrent_signups_never_exceed_capacity(db, cap):
    cap(3)
    results, errors = [], []

    def go(i):
        try:
            results.append(create_user(f"u{i}@example.com", consent=True))
        except CapacityFull:
            results.append("full")
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert results.count("full") == 9 and len([r for r in results if r != "full"]) == 3
    assert db.scalar(select(func.count(User.id))) == 3


def test_same_person_double_submit_creates_one_account(db, cap):
    cap(5)
    a = create_user("dup@example.com", google_sub="g-dup", consent=True)
    b = create_user("dup@example.com", google_sub="g-dup", consent=True)
    assert a == b and users(db) == ["dup@example.com"]


# ------------------------------------------------------------------ 동의
def test_no_user_without_consent(db, user):
    with TestClient(app) as c:
        c.post("/auth/dev", data={"email": "n@example.com"})
        page = c.get("/consent").text
        for name in ("agree_terms", "agree_privacy", "agree_age"):
            assert f'name="{name}" required' in page
        assert 'href="/terms"' in page and 'href="/privacy"' in page
        r = c.post("/consent", data={"agree_terms": "on", "agree_privacy": "on", "csrf": csrf(c)})  # 14세 확인 빠짐
        assert r.status_code == 400 and "필수 항목" in r.text
        assert c.get("/today", follow_redirects=False).headers["location"] == "/login"  # 로그인되지 않음
        c.post("/consent/decline")
        assert c.get("/consent").status_code == 400  # 가입 대기 정보도 지워짐
    assert "n@example.com" not in users(db)


def test_pending_signup_expires_after_10_minutes(db, user, monkeypatch):
    with TestClient(app) as c:
        c.post("/auth/dev", data={"email": "slow@example.com"})
        token = csrf(c)  # 동의 화면을 연 뒤
        later = clock.now() + timedelta(minutes=11)
        monkeypatch.setattr(clock, "now", lambda: later)  # 11분이 지나 제출
        r = c.post("/consent", data={**AGREE, "csrf": token})
        assert r.status_code == 400 and "시간이 지났어요" in r.text
    assert "slow@example.com" not in users(db)


def test_consent_record_saved(db, user):
    with TestClient(app) as c:
        signup(c, "rec@example.com")
    u = db.scalar(select(User).where(User.email == "rec@example.com"))
    assert (u.terms_version, u.privacy_version) == (legal.terms().version, legal.privacy().version)
    assert u.agreed_at is not None


def test_existing_user_must_consent_once_on_any_page(db, user, cap):
    cap(0)  # 정원이 0 이어도 기존 사용자 재동의에는 정원 검사가 없다
    old = member(db, "old@example.com", consent=False)
    with TestClient(app) as c:
        login(c, "old@example.com", agree=False)
        for path in ("/today", "/", "/settings", "/calendar", "/push/key"):
            assert c.get(path, follow_redirects=False).headers["location"] == "/consent", path
        assert c.get("/account").status_code == 200  # 동의하지 않고 계정을 지울 수 있게
        page = c.get("/consent").text
        assert "이용 동의를 받고 있어요" in page and "계정 삭제" in page
        assert c.post("/consent", data={**AGREE, "csrf": csrf(c)}, follow_redirects=False).headers["location"] == "/today"
        assert c.get("/", follow_redirects=False).status_code == 200
    db.refresh(old)
    assert old.agreed_at is not None


def test_version_bump_requires_consent_again(db, user, monkeypatch):
    with TestClient(app) as c:
        login(c)
        assert c.get("/", follow_redirects=False).status_code == 200
        bumped = replace(legal.terms(), version="2.0")
        monkeypatch.setattr(legal, "terms", lambda: bumped)
        assert c.get("/", follow_redirects=False).headers["location"] == "/consent"
        assert "약관이 바뀌었어요" in c.get("/consent").text
        c.post("/consent", data={**AGREE, "csrf": csrf(c)})
        assert c.get("/", follow_redirects=False).status_code == 200
    db.refresh(user)
    assert user.terms_version == "2.0"


def test_admin_also_consents_and_admin_page_shows_capacity(db, user, cap):
    cap(7)
    user.agreed_at = None
    db.commit()
    member(db, "a@example.com")
    with TestClient(app) as c:
        login(c, agree=False)
        assert c.get("/admin", follow_redirects=False).headers["location"] == "/consent"
        c.post("/consent", data={**AGREE, "csrf": csrf(c)})
        assert "1 / 7명" in c.get("/admin").text


# ------------------------------------------------------------------ 문서
def test_terms_and_privacy_pages_render_from_content():
    with TestClient(app) as c:
        for path, doc in (("/terms", legal.terms()), ("/privacy", legal.privacy())):
            html = c.get(path).text
            assert doc.title in html and f"버전 {doc.version}" in html and doc.effective_date in html
        terms, privacy = c.get("/terms").text, c.get("/privacy").text
        assert "제1조 (목적)" in terms and "임시 본문" not in terms + privacy  # 최종본
        assert terms.count("<h1>") == 1 and privacy.count("<h1>") == 1  # 제목은 한 번만 (본문 첫 # 제목을 문서 제목으로)
        assert "서명된 세션 쿠키에 10분 동안만" in privacy and "<table>" in privacy
        assert legal.terms().version == legal.privacy().version == "2026-11-26"
        login_html = c.get("/login").text
        assert 'href="/terms"' in login_html and 'href="/privacy"' in login_html


def test_settings_has_legal_links(db, user):
    with TestClient(app) as c:
        login(c)
        html = c.get("/settings").text
    assert 'href="/terms"' in html and 'href="/privacy"' in html


def test_markdown_is_rendered_safely(tmp_path, monkeypatch):
    (tmp_path / "evil.md").write_text(
        "---\ntitle: T\nversion: 1\n---\n<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n<img src=x onerror=alert(1)>",
        encoding="utf-8",
    )
    monkeypatch.setattr(legal, "CONTENT", tmp_path)
    legal.load.cache_clear()
    try:
        doc = legal.load("evil")
    finally:
        legal.load.cache_clear()
    assert "<script" not in doc.html and "<img" not in doc.html and 'href="javascript:' not in doc.html
    assert doc.version == "1" and doc.title == "T"


def test_version_is_required(tmp_path, monkeypatch):
    (tmp_path / "x.md").write_text("---\ntitle: T\n---\n본문", encoding="utf-8")
    monkeypatch.setattr(legal, "CONTENT", tmp_path)
    legal.load.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            legal.load("x")
    finally:
        legal.load.cache_clear()


# ------------------------------------------------------------------ 0008
def test_migration_0008_keeps_rows_and_drops_mail_columns(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    path = tmp_path / "app.db"
    url = f"sqlite:///{path}"
    command.upgrade(migrate.alembic_config(url), "0007")
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO users (id, email, google_sub, name, timezone, notify_channel, notify_email, sentence_cursor, "
        "onboarding_step, last_prepared_date, is_admin, created_at, last_login_at) "
        "VALUES (1, 'u@example.com', 'g', 'U', 'Asia/Seoul', 'push', '', 4, 3, NULL, 0, '2026-11-01 00:00:00.000000', NULL)"
    )
    con.execute("INSERT INTO tasks (id, user_id, name, subject, duration_min, weekly_count, active) VALUES (1, 1, 'R', '', 30, 3, 1)")
    con.execute(
        "INSERT INTO assignments (id, user_id, week_start, date, task_id, kind, status, makeup_of_id, position, recorded_at) "
        "VALUES (1, 1, '2026-11-02', '2026-11-02', 1, 'regular', 'done', NULL, 0, NULL)"
    )
    con.execute("INSERT INTO action_tokens (token, assignment_id, action, expires_at, used_at) VALUES ('t', 1, 'done', '2026-12-01 00:00:00', NULL)")
    con.execute("INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent, created_at, last_error) VALUES (1, 'https://x/1', 'p', 'a', '', '2026-11-01 00:00:00', '')")
    con.commit()
    con.close()
    assert migrate.run(url).startswith("upgraded 0007 -> ")
    con = sqlite3.connect(path)
    cols = {r[1] for r in con.execute("pragma table_info(users)")}
    assert {"terms_version", "privacy_version", "agreed_at"} <= cols and not cols & {"notify_channel", "notify_email"}
    assert con.execute("select email, name, sentence_cursor, terms_version from users").fetchall() == [("u@example.com", "U", 4, None)]
    assert con.execute("select count(*) from tasks").fetchone()[0] == 1  # users 를 다시 만들어도 연쇄 삭제 없음
    assert con.execute("select count(*) from assignments").fetchone()[0] == 1
    assert con.execute("select count(*) from push_subscriptions").fetchone()[0] == 1
    assert not con.execute("select name from sqlite_master where name='action_tokens'").fetchall()
    assert con.execute("pragma foreign_key_check").fetchall() == []
    con.close()
    assert list((tmp_path / "backups").glob("app-before-0008-*.db"))  # 직전 자동 백업
