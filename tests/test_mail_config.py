"""메일 설정 판단 · 시작 로그(설정 여부만) · 설정 화면 테스트 메일(오류 코드만)."""
import smtplib

import pytest
from fastapi.testclient import TestClient

from app import config
from app.config import env_presence, env_presence_line, settings
from app.main import app
from app.services import mailer
from tests.conftest import login

SECRET = "abcd efgh ijkl mnop"
ADDR = "sender-secret@example.com"


@pytest.mark.parametrize("raw, present", [
    (None, False), ("", False), ("   ", False), ('""', False), ("''", False),
    ("GMAIL_APP_PASSWORD=", False), (SECRET, True), (f'  "{SECRET}" ', True),
])
def test_presence_rule(monkeypatch, raw, present):
    if raw is None:
        monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("GMAIL_APP_PASSWORD", raw)
    assert env_presence(("GMAIL_APP_PASSWORD",)) == {"GMAIL_APP_PASSWORD": present}
    # 설정 화면 판단과 같은 규칙
    assert bool(config._secret_env("GMAIL_APP_PASSWORD")) is present


def test_empty_mail_backend_means_console(monkeypatch):
    for raw in ("", "  ", " SMTP "):
        monkeypatch.setenv("MAIL_BACKEND", raw)
        assert (config._secret_env("MAIL_BACKEND").lower() or "console") == ("smtp" if raw.strip() else "console")


def test_startup_log_has_presence_only(monkeypatch, capsys):
    monkeypatch.setenv("GMAIL_ADDRESS", ADDR)
    monkeypatch.setenv("GMAIL_APP_PASSWORD", SECRET)
    monkeypatch.delenv("MAIL_BACKEND", raising=False)
    line = env_presence_line()
    assert line == "[config] GMAIL_ADDRESS=설정됨 GMAIL_APP_PASSWORD=설정됨 MAIL_BACKEND=없음"
    with TestClient(app):
        pass
    out = capsys.readouterr().out
    assert line in out
    assert SECRET not in out and SECRET.replace(" ", "") not in out and ADDR not in out


@pytest.fixture()
def smtp_mode(monkeypatch):
    monkeypatch.setattr(settings, "mail_backend", "smtp")
    monkeypatch.setattr(settings, "gmail_address", ADDR)
    monkeypatch.setattr(settings, "gmail_app_password", SECRET)


class FakeSMTP:
    error: Exception | None = None

    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def login(self, *a):
        if FakeSMTP.error: raise FakeSMTP.error
    def send_message(self, msg): pass


@pytest.mark.parametrize("error, code", [
    (None, None),
    (smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted " + SECRET.encode()), "MAIL_AUTH_FAILED"),
    (OSError(f"connect to {ADDR} refused"), "MAIL_CONNECT_FAILED"),
    (smtplib.SMTPDataError(550, b"rejected"), "MAIL_REJECTED"),
    (ValueError(f"weird {SECRET}"), "MAIL_UNKNOWN"),
])
def test_settings_test_mail_shows_code_only(db, user, smtp_mode, monkeypatch, capsys, error, code):
    FakeSMTP.error = error
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    with TestClient(app) as c:
        login(c)
        r = c.post("/settings/mail/test", follow_redirects=False)
        assert r.headers["location"] == f"/settings?mail_test={code or 'ok'}#mail"
        page = c.get(r.headers["location"]).text
    if code:
        assert f'오류 코드 <code class="mail-error-code">{code}</code>' in page
    else:
        assert "테스트 메일을 보냈습니다." in page
    for leak in (SECRET, SECRET.replace(" ", ""), "Username and Password", "refused", "weird", "rejected"):
        assert leak not in page
    out = capsys.readouterr().out
    assert SECRET not in out and SECRET.replace(" ", "") not in out


def test_missing_password_code(db, user, monkeypatch):
    monkeypatch.setattr(settings, "mail_backend", "smtp")
    monkeypatch.setattr(settings, "gmail_app_password", "")
    with TestClient(app) as c:
        login(c)
        page = c.post("/settings/mail/test").text
    assert "MAIL_NOT_CONFIGURED" in page and "GMAIL_APP_PASSWORD 미설정" in page


def test_console_mode_test_mail(db, user):
    with TestClient(app) as c:
        login(c)
        page = c.post("/settings/mail/test").text
    assert "console 모드라 실제로 보내지 않고 저장했습니다" in page


def test_unknown_code_in_query_is_not_echoed(db, user):
    with TestClient(app) as c:
        login(c)
        page = c.get("/settings?mail_test=<b>hello</b>").text
    assert "hello" not in page
