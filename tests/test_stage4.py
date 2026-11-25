"""4단계: 운영 모드 잠금 · 요청 횟수 제한 · 입력 제한 · 보안 헤더 · 요청 위조 방어 · 로그 가림."""
import logging
import os
import subprocess
import sys
from datetime import datetime, time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import security
from app.main import app
from app.models import NotifyLog, PushSubscription, Sentence, Task
from app.services import google_auth, push
from app.services.daily import run_due
from tests.conftest import add_sub, login
from tests.test_auth import google, start  # noqa: F401,F811  (google 은 fixture)
from tests.test_stage2 import MON, setup_day

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ 운영 모드
def test_production_mode_hides_docs_and_dev_login():
    """운영 설정(RENDER=true)으로 앱을 새로 띄워: /docs·/redoc·/openapi.json 없음, 개발용 로그인 404."""
    code = r'''
import os
os.environ.update(RENDER="true", SECRET_KEY="x" * 40, DEV_LOGIN="false", SCHEDULER_ENABLED="false",
                  VAPID_PUBLIC_KEY="p", VAPID_PRIVATE_KEY="k", VAPID_SUBJECT="mailto:a@b.c",
                  DATABASE_URL="sqlite:///" + os.environ["TMPDB"], BASE_URL="https://app.example")
from fastapi.testclient import TestClient
from app.main import app
assert app.docs_url is None and app.redoc_url is None and app.openapi_url is None
with TestClient(app, base_url="https://app.example", headers={"origin": "https://app.example"}) as c:
    for p in ("/docs", "/redoc", "/openapi.json"):
        r = c.get(p)  # 경로 자체가 없음 (로그인 안 됐으면 로그인 화면으로, 문서 내용은 절대 없음)
        assert "swagger" not in r.text.lower() and '"openapi"' not in r.text and "redoc" not in r.text.lower(), p
    assert c.post("/auth/dev", data={"email": "a@b.c"}).status_code == 404
    r = c.get("/login")
    assert "개발용 로그인" not in r.text
    assert "max-age=31536000" in r.headers["strict-transport-security"]
    r = c.get("/login", headers={"x-forwarded-proto": "http"}, follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"].startswith("https://")
print("ok")
'''
    env = {**os.environ, "TMPDB": str(ROOT / "data" / "prod-mode-test.db")}
    try:
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    finally:
        for suffix in ("", "-journal"):
            p = ROOT / "data" / f"prod-mode-test.db{suffix}"
            if p.exists():
                p.unlink()
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout + r.stderr


def test_dev_mode_still_has_docs_locally():
    with TestClient(app) as c:
        assert c.get("/openapi.json").status_code == 200  # 로컬·테스트에서만


# ------------------------------------------------------------------ 요청 횟수 제한
def test_login_rate_limit(db):
    with TestClient(app) as c:
        codes = [c.get("/auth/google/start", follow_redirects=False).status_code for _ in range(11)]
    assert codes[-1] == 429 and 429 not in codes[:10]


def test_login_limit_page_is_friendly(db):
    with TestClient(app) as c:
        for _ in range(10):
            c.post("/auth/dev", data={"email": "x@example.com", "agree": "1"})
        r = c.post("/auth/dev", data={"email": "x@example.com", "agree": "1"})
    assert r.status_code == 429 and "로그인 시도가 너무 많아요" in r.text and r.headers["retry-after"] == "60"


def test_login_limit_is_per_ip(db):
    with TestClient(app) as c:
        for _ in range(10):
            c.get("/auth/google/start", headers={"x-forwarded-for": "1.1.1.1"}, follow_redirects=False)
        assert c.get("/auth/google/start", headers={"x-forwarded-for": "1.1.1.1"}, follow_redirects=False).status_code == 429
        # 맨 앞 값을 꾸며도 Render 가 붙인 마지막 값(실제 IP)으로 센다
        assert c.get("/auth/google/start", headers={"x-forwarded-for": "9.9.9.9, 1.1.1.1"}, follow_redirects=False).status_code == 429
        assert c.get("/auth/google/start", headers={"x-forwarded-for": "2.2.2.2"}, follow_redirects=False).status_code != 429


def test_test_push_5_per_day(db, user, pusher):
    add_sub(db, user, "https://web.push.apple.com/me")
    with TestClient(app) as c:
        login(c)
        oks = [c.post("/push/test").status_code for _ in range(5)]
        r = c.post("/push/test")
    assert oks == [200] * 5 and len(pusher["calls"]) == 5
    assert r.status_code == 429 and "하루 5번" in r.json()["message"]


def test_subscribe_and_record_limits(db, user):
    body = {"endpoint": "https://web.push.apple.com/x", "keys": {"p256dh": "p" * 20, "auth": "a" * 16}}
    with TestClient(app) as c:
        login(c)
        codes = [c.post("/push/subscribe", json=body).status_code for _ in range(11)]
        assert codes[-1] == 429 and "알림 설정 요청이 너무 많아요" in c.post("/push/subscribe", json=body).json()["message"]
        security.window.clear()
        codes = [c.post("/assignments/999999/status", data={"status": "done"}).status_code for _ in range(61)]
        assert codes[-1] == 429 and set(codes[:60]) == {404}


# ------------------------------------------------------------------ 입력 길이·개수
def test_task_limits(db, user):
    with TestClient(app) as c:
        login(c)
        r = c.post("/tasks", data={"name": "가" * 51, "duration_min": 30, "weekly_count": 3}, follow_redirects=False)
        assert r.headers["location"] == "/tasks?task_error=length"
        assert "과제명은 1~50자" in c.get(r.headers["location"]).text
        assert c.post("/tasks", data={"name": "x", "subject": "s" * 21, "duration_min": 30, "weekly_count": 3},
                      follow_redirects=False).headers["location"].endswith("task_error=length")
        for i in range(30):
            c.post("/tasks", data={"name": f"t{i}", "duration_min": 10, "weekly_count": 1})
        r = c.post("/tasks", data={"name": "t31", "duration_min": 10, "weekly_count": 1}, follow_redirects=False)
        assert r.headers["location"] == "/tasks?task_error=count"
    assert len(db.scalars(select(Task).where(Task.user_id == user.id)).all()) == 30


def test_sentence_limits(db, user):
    with TestClient(app) as c:
        login(c)
        before = len(db.scalars(select(Sentence).where(Sentence.user_id == user.id)).all())
        r = c.post("/sentences", data={"body": "\n".join(f"s{i}" for i in range(201))}, follow_redirects=False)
        assert r.headers["location"] == "/sentences?error=count"
        r = c.post("/sentences", data={"body": "a" * 301}, follow_redirects=False)
        assert r.headers["location"] == "/sentences?error=length"
        assert "300자까지" in c.get("/sentences?error=length").text
    db.expire_all()
    assert len(db.scalars(select(Sentence).where(Sentence.user_id == user.id)).all()) == before  # 그대로


def test_push_device_cap(db, user):
    with TestClient(app) as c:
        login(c)
        for i in range(11):
            security.window.clear()
            body = {"endpoint": f"https://web.push.apple.com/d{i}", "keys": {"p256dh": "p" * 20, "auth": "a" * 16}}
            assert c.post("/push/subscribe", json=body).status_code == 200
    eps = {s.endpoint for s in db.scalars(select(PushSubscription).where(PushSubscription.user_id == user.id))}
    assert len(eps) == 10 and "https://web.push.apple.com/d10" in eps and "https://web.push.apple.com/d0" not in eps


# ------------------------------------------------------------------ 보안 헤더 · 요청 위조
def test_security_headers():
    with TestClient(app) as c:
        r = c.get("/login")
        h = r.headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "script-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
        assert "strict-transport-security" not in h  # 운영(https)에서만
        assert c.get("/auth/handoff").headers["referrer-policy"] == "no-referrer"
        assert c.get("/today", follow_redirects=False).headers["x-frame-options"] == "DENY"  # 리디렉션에도


def test_csrf_origin_check(db, user):
    with TestClient(app) as c:
        login(c)
        ok = c.post("/tasks", data={"name": "ok", "duration_min": 10, "weekly_count": 1}, follow_redirects=False)
        assert ok.status_code == 303
        evil = c.post("/tasks", data={"name": "evil", "duration_min": 10, "weekly_count": 1},
                      headers={"origin": "https://evil.example"})
        assert evil.status_code == 403
        del c.headers["origin"]
        assert c.post("/tasks", data={"name": "none", "duration_min": 10, "weekly_count": 1}).status_code == 403
        r = c.post("/tasks", data={"name": "ref", "duration_min": 10, "weekly_count": 1},
                   headers={"referer": "http://testserver/tasks"}, follow_redirects=False)
        assert r.status_code == 303  # Origin 이 없어도 같은 사이트 Referer 면 허용
        assert c.post("/push/test", headers={"origin": "https://evil.example"}).json()["ok"] is False
    names = {t.name for t in db.scalars(select(Task).where(Task.user_id == user.id))}
    assert names == {"ok", "ref"}


def test_form_tokens_on_delete_and_consent(db, user):
    with TestClient(app) as c:
        login(c)
        r = c.post("/account/delete", data={"confirm_email": "me@example.com"}, follow_redirects=False)
        assert r.headers["location"] == "/account?error=1"  # 토큰 없음 → 삭제 안 됨
        r = c.post("/account/delete", data={"confirm_email": "me@example.com", "csrf": "wrong"}, follow_redirects=False)
        assert r.headers["location"] == "/account?error=1"
        assert 'name="csrf"' in c.get("/account").text
        c.post("/logout")
        c.post("/auth/dev", data={"email": "n@example.com"})
        r = c.post("/consent", data={"agree_terms": "on", "agree_privacy": "on", "agree_age": "on"})
        assert r.status_code == 400 and "화면이 오래되었어요" in r.text
    db.expire_all()
    assert db.get(type(user), user.id) is not None


# ------------------------------------------------------------------ 로그
def test_access_log_hides_ip_and_oauth_values():
    f = security.AccessLogFilter()
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("203.0.113.7:5555", "GET", "/auth/google/callback?state=abc&code=4/0Ab-secret&scope=email", "1.1", 303), None)
    f.filter(rec)
    line = rec.getMessage()
    assert "203.0.113.7" not in line and "abc" not in line and "4/0Ab-secret" not in line
    assert "state=***" in line and "code=***" in line and "scope=email" in line
    assert logging.getLogger("uvicorn.access").filters  # 앱 시작 시 설치됨


def test_push_errors_store_codes_only(db, user, pusher, monkeypatch, capsys):
    ep = "https://web.push.apple.com/secret-device-token"
    setup_day(db, user)
    add_sub(db, user, ep)

    def boom(subscription_info, **kw):
        raise ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: {subscription_info['endpoint']}")

    monkeypatch.setattr(push, "webpush", boom)
    from app import scheduler

    monkeypatch.setattr(scheduler.clock, "now", lambda: datetime.combine(MON, time(7)))
    scheduler.tick()
    db.expire_all()
    sub = db.scalar(select(PushSubscription))
    log = db.scalar(select(NotifyLog))
    out = capsys.readouterr().out
    for text in (sub.last_error, log.error, out):
        assert "secret-device-token" not in text and "web.push.apple.com" not in text
    assert sub.last_error == "ConnectionError" and "ConnectionError" in log.error


def test_push_http_error_code_only(db, user, pusher):
    setup_day(db, user)
    add_sub(db, user, "https://web.push.apple.com/tok-123")
    pusher["fail"]["https://web.push.apple.com/tok-123"] = 503
    run_due(db, datetime.combine(MON, time(7)))
    db.expire_all()
    assert db.scalar(select(PushSubscription)).last_error == "HTTP 503"
    assert "tok-123" not in db.scalar(select(NotifyLog)).error


def test_google_failure_logs_error_kind_only(db, google, monkeypatch, capsys):  # noqa: F811
    async def bad_verify(*a, **kw):
        err = ValueError("token for leak@example.com with secret GOCSPX-abc")
        err.error = "invalid_grant"
        raise err

    monkeypatch.setattr(google_auth, "verify", bad_verify)
    with TestClient(app) as c:
        state = start(c)
        r = c.get(f"/auth/google/callback?state={state}&code=ok")
        r2 = c.get("/auth/google/callback?state=x&error=access_denied%22%3E%3Cscript%3E")
    out = capsys.readouterr().out
    assert "verify failed: ValueError code=invalid_grant" in out
    assert "leak@example.com" not in out and "GOCSPX" not in out
    assert "invalid_grant" in r.text and "leak@" not in r.text
    assert "error=access_deniedscript" in out and '"><script>' not in r2.text  # 로그·화면 모두 영문·숫자·밑줄만


def test_no_test_labels_except_test_buttons(db, user):
    with TestClient(app) as c:
        login(c)
        for path in ("/today", "/", "/calendar", "/settings", "/tasks", "/sentences", "/account"):
            html = c.get(path).text
            assert html.count("테스트") == html.count("테스트 알림 보내기"), path
