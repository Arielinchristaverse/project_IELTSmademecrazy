"""실제 Authlib 경로를 가짜 OIDC 서버로 시험 (모킹 없이 HTTP 로 토큰 교환 + ID 토큰 검증)."""
import asyncio
import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import uvicorn
from authlib.integrations.starlette_client import OAuth

from app.auth import _error_code
from app.services import google_auth
from tests.support import fake_idp

REDIRECT = "https://app.example/auth/google/callback"


@pytest.fixture(scope="module")
def idp():
    server = uvicorn.Server(uvicorn.Config(fake_idp.app, host="127.0.0.1", port=8765, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    yield fake_idp.ISS
    server.should_exit = True
    t.join(timeout=5)


def run_login(monkeypatch, secret: str):
    o = OAuth()
    o.register(
        "google",
        client_id="cid",
        client_secret=secret,
        server_metadata_url=fake_idp.ISS + "/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )
    monkeypatch.setattr(google_auth, "_oauth", o)

    async def go():
        a = await google_auth.authorization(REDIRECT)
        q = parse_qs(urlparse(a["url"]).query)
        assert q["scope"] == ["openid email profile"] and q["code_challenge_method"] == ["S256"]
        httpx.post(fake_idp.ISS + "/register", data={"code": "c1", "nonce": q["nonce"][0], "challenge": q["code_challenge"][0]})
        return await google_auth.verify(REDIRECT, "c1", a["code_verifier"], a["nonce"])

    return asyncio.run(go())


def test_real_token_exchange_and_id_token_verification(idp, monkeypatch):
    claims = run_login(monkeypatch, "secret")
    assert claims["email"] == "me@example.com" and claims["email_verified"] and claims["sub"] == "g-1"


def test_wrong_secret_gives_readable_error_code(idp, monkeypatch):
    with pytest.raises(Exception) as e:
        run_login(monkeypatch, "wrong")
    assert _error_code(e.value) == "invalid_client"


def test_pasted_env_values_are_cleaned(monkeypatch):
    from app.config import _secret_env

    for raw in ["GOCSPX-abc", " GOCSPX-abc ", '"GOCSPX-abc"', "GOOGLE_CLIENT_SECRET=GOCSPX-abc", "GOOGLE_CLIENT_SECRET= 'GOCSPX-abc'\n"]:
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", raw)
        assert _secret_env("GOOGLE_CLIENT_SECRET") == "GOCSPX-abc", raw
