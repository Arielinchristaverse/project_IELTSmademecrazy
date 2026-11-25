"""설치 안내 · 배포 설정 점검."""
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parent.parent
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Version/17.4 Mobile/15E148 Safari/604.1"


def test_install_page_variants():
    with TestClient(app) as c:
        html = c.get("/install", headers={"user-agent": IPHONE}).text
        assert "홈 화면에 추가" in html and "Google 계정으로 로그인" in html and "따로 로그인" in html
        assert "앱 안의 브라우저" not in html
        kakao = c.get("/install", headers={"user-agent": IPHONE + " KAKAOTALK 10.5.0"}).text
        assert "Safari로 열기" in kakao
        chrome = c.get("/install", headers={"user-agent": IPHONE.replace("Version/17.4", "CriOS/124")}).text
        assert "브라우저의 <b>공유 버튼</b>" in chrome
        assert c.get("/install", follow_redirects=False).status_code == 200  # 로그인 없이 볼 수 있음


def test_login_page_loads_install_redirect_script():
    with TestClient(app) as c:
        html = c.get("/login").text
    assert "/static/login.js" in html


def test_login_js_ios_detection_in_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    r = subprocess.run([node, str(ROOT / "tests/js/login.test.js")], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr


def test_render_yaml_declares_all_settings():
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    for key in [
        "ADMIN_EMAIL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
        "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT", "SECRET_KEY", "DATABASE_URL", "MAX_USERS",
    ]:
        assert f"key: {key}" in text, key
    for gone in ("MAIL_BACKEND", "GMAIL_", "NOTIFY_CHANNELS", "MAIL_DAILY_LIMIT", "DEV_LOGIN", "ALLOWED_EMAILS"):  # 푸시 전용 · 정원제
        assert gone not in text, gone
    assert "name: afterwork" in text and "mountPath: /var/data" in text
