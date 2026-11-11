"""구글 로그인(모킹) · iOS 쿠키 분리 대비 넘겨받기 · 가입 제한 · 온보딩 · 계정 삭제."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.config import settings
from app.main import app
from app.models import Assignment, LoginRequest, Task, User
from app.services import google_auth
from tests.conftest import login


@pytest.fixture()
def google(monkeypatch):
    """Authlib 호출을 가짜로 바꾼다. claims 를 바꿔 끼워 여러 계정을 흉내 낸다."""
    box = {"claims": {"sub": "g-1", "email": "new@example.com", "email_verified": True, "name": "새 사용자"}}
    seen = {}
    counter = {"n": 0}

    async def fake_authorization(redirect_uri):
        counter["n"] += 1
        n = counter["n"]
        return {
            "url": f"https://accounts.google.com/o/oauth2/auth?state=st{n}",
            "state": f"st{n}",
            "code_verifier": "v" * 48,
            "nonce": f"nonce{n}",
        }

    async def fake_verify(redirect_uri, code, code_verifier, nonce):
        seen[code] = (redirect_uri, code_verifier, nonce)
        if code == "bad":
            raise ValueError("invalid")
        return dict(box["claims"])

    monkeypatch.setattr(google_auth, "authorization", fake_authorization)
    monkeypatch.setattr(google_auth, "verify", fake_verify)
    monkeypatch.setattr(google_auth, "is_configured", lambda: True)
    box["seen"] = seen
    return box


def start(c):
    r = c.get("/auth/google/start", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("https://accounts.google.com/")
    return r.headers["location"].split("state=")[1]


def test_google_login_same_browser_creates_user_and_goes_to_onboarding(db, google):
    with TestClient(app) as c:
        state = start(c)
        r = c.get(f"/auth/google/callback?state={state}&code=ok", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
        assert c.get("/", follow_redirects=False).headers["location"] == "/onboarding"
    u = db.scalar(select(User).where(User.email == "new@example.com"))
    assert u.google_sub == "g-1" and u.name == "새 사용자" and not u.is_admin
    # PKCE·nonce 가 DB에 저장된 값으로 검증에 쓰였는지
    assert google["seen"]["ok"] == (f"{settings.base_url}/auth/google/callback", "v" * 48, "nonce1")


def test_login_finished_in_other_cookie_jar_is_handed_off_once(db, google):
    """iOS: 홈 화면 앱에서 시작 → 다른 저장소(Safari 등)에서 콜백 → 앱으로 돌아와 넘겨받기."""
    with TestClient(app) as app_client, TestClient(app) as other_browser:
        state = start(app_client)
        r = other_browser.get(f"/auth/google/callback?state={state}&code=ok")
        assert r.status_code == 200 and "로그인 완료" in r.text and "돌아가세요" in r.text
        assert other_browser.get("/", follow_redirects=False).headers["location"] == "/login"  # 거기선 로그인 안 됨

        assert app_client.get("/auth/handoff").json() == {"ok": True}
        assert app_client.get("/onboarding", follow_redirects=False).status_code == 303
        assert app_client.get("/onboarding/shifts").status_code == 200
        assert app_client.get("/auth/handoff").json() == {"ok": False}  # 1회용


def test_handoff_expires(db, google, monkeypatch):
    with TestClient(app) as app_client, TestClient(app) as other:
        state = start(app_client)
        other.get(f"/auth/google/callback?state={state}&code=ok")
        lr = db.scalar(select(LoginRequest).where(LoginRequest.state == state))
        lr.created_at = clock.now() - timedelta(minutes=11)
        db.commit()
        assert app_client.get("/auth/handoff").json() == {"ok": False}


def test_callback_rejects_unknown_state_reuse_and_bad_token(db, google):
    with TestClient(app) as c:
        assert c.get("/auth/google/callback?state=nope&code=ok").status_code == 400
        state = start(c)
        assert c.get(f"/auth/google/callback?state={state}&code=bad").status_code == 400
        state = start(c)
        c.get(f"/auth/google/callback?state={state}&code=ok")
        c.post("/logout")
        assert c.get(f"/auth/google/callback?state={state}&code=ok").status_code == 400  # 재사용 불가


def test_unverified_email_and_allowlist(db, google, monkeypatch):
    with TestClient(app) as c:
        google["claims"]["email_verified"] = False
        state = start(c)
        assert c.get(f"/auth/google/callback?state={state}&code=ok").status_code == 403

        google["claims"]["email_verified"] = True
        monkeypatch.setattr(type(settings), "allowed_emails", frozenset({"friend@example.com"}))
        state = start(c)
        r = c.get(f"/auth/google/callback?state={state}&code=ok")
        assert r.status_code == 403 and "초대" in r.text
        assert db.scalar(select(User).where(User.email == "new@example.com")) is None

        google["claims"].update(email="Friend@Example.com", sub="g-2")
        state = start(c)
        assert c.get(f"/auth/google/callback?state={state}&code=ok", follow_redirects=False).status_code == 303


def test_admin_first_google_login_links_existing_account(db, user, google):
    """마이그레이션으로 만들어진 관리자 계정(google_sub 없음)에 첫 구글 로그인이 연결된다."""
    google["claims"].update(email="me@example.com", sub="g-admin")
    with TestClient(app) as c:
        state = start(c)
        c.get(f"/auth/google/callback?state={state}&code=ok")
        assert c.get("/", follow_redirects=False).status_code == 200  # 온보딩 완료된 기존 계정
    db.refresh(user)
    assert user.google_sub == "g-admin" and user.is_admin
    assert db.scalar(select(User.id).where(User.email == "me@example.com")) == user.id


def test_onboarding_flow(db):
    with TestClient(app) as c:
        login(c, "fresh@example.com")
        assert c.get("/tasks", follow_redirects=False).status_code == 200  # 온보딩 중 허용
        assert c.get("/", follow_redirects=False).headers["location"] == "/onboarding"
        assert c.get("/onboarding", follow_redirects=False).headers["location"] == "/onboarding/shifts"

        form = {f"{code}_minutes": "60" for code in ["DAY", "EVENING", "NIGHT", "OFF"]}
        form.update({f"{code}_time": "07:30" for code in ["DAY", "EVENING", "NIGHT", "OFF"]})
        assert c.post("/onboarding/shifts", data=form, follow_redirects=False).headers["location"] == "/onboarding/tasks"

        r = c.post("/onboarding/tasks/next", follow_redirects=False)
        assert r.headers["location"] == "/onboarding/tasks?error=1"  # 과제 없이 넘어갈 수 없음
        r = c.post(
            "/tasks",
            data={"name": "단어", "duration_min": 30, "weekly_count": 3, "back": "/onboarding/tasks"},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/onboarding/tasks"
        assert c.post("/onboarding/tasks/next", follow_redirects=False).headers["location"] == "/onboarding/calendar"

        today = clock.today()
        r = c.post(
            "/calendar",
            data={"month": f"{today:%Y-%m}", f"shift_{today.isoformat()}": "DAY", "back": "/onboarding/calendar"},
            follow_redirects=False,
        )
        assert r.headers["location"].startswith("/onboarding/calendar?month=")
        assert c.post("/onboarding/finish", follow_redirects=False).headers["location"].startswith("/?msg=")
        assert c.get("/", follow_redirects=False).status_code == 200

    u = db.scalar(select(User).where(User.email == "fresh@example.com"))
    db.refresh(u)
    assert u.onboarding_step == 3
    assert db.scalar(select(Task).where(Task.user_id == u.id)).name == "단어"
    assert db.scalar(select(Assignment).where(Assignment.user_id == u.id, Assignment.date == today))


def test_open_redirect_blocked(db, user):
    with TestClient(app) as c:
        login(c)
        r = c.post(
            "/tasks",
            data={"name": "x", "duration_min": 5, "weekly_count": 1, "back": "//evil.example.com"},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/tasks"


def test_account_delete(db, user):
    uid = user.id
    with TestClient(app) as c:
        login(c)
        r = c.post("/account/delete", data={"confirm_email": "wrong@example.com"}, follow_redirects=False)
        assert r.headers["location"] == "/account?error=1"
        assert db.get(User, uid) is not None
        r = c.post("/account/delete", data={"confirm_email": "ME@example.com"}, follow_redirects=False)
        assert r.headers["location"] == "/login?deleted=1"
        assert c.get("/", follow_redirects=False).headers["location"] == "/login"
    db.expire_all()
    assert db.get(User, uid) is None


def test_privacy_page_public():
    with TestClient(app) as c:
        r = c.get("/privacy")
        assert r.status_code == 200 and "개인정보처리방침" in r.text
