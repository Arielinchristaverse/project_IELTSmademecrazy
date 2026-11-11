"""PWA 기본 요소: 매니페스트, 서비스 워커, iOS 메타 태그, 아이콘."""
import struct
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

ICONS = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_manifest():
    with TestClient(app) as c:
        r = c.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert m["display"] == "standalone" and m["scope"] == "/" and m["start_url"].startswith("/today")
    assert m["theme_color"] == "#1f2a44" and m["background_color"] == "#ffffff"
    purposes = {i["purpose"] for i in m["icons"]}
    assert purposes == {"any", "maskable"}
    for icon in m["icons"]:
        w, h = png_size(ICONS / Path(icon["src"]).name)
        assert f"{w}x{h}" == icon["sizes"]


def test_service_worker_served_from_root():
    with TestClient(app) as c:
        r = c.get("/sw.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert r.headers["service-worker-allowed"] == "/" and r.headers["cache-control"] == "no-cache"
    js = r.text
    assert "showNotification" in js and "notificationclick" in js and '"/today"' in js
    assert "actions" not in js  # 알림 액션 버튼은 쓰지 않음 (iOS 미지원)


def test_ios_meta_and_apple_touch_icon():
    assert png_size(ICONS / "icon-180.png") == (180, 180)
    with TestClient(app) as c:
        html = c.get("/login").text
    for needle in [
        'rel="manifest" href="/manifest.webmanifest"',
        'name="apple-mobile-web-app-capable" content="yes"',
        'rel="apple-touch-icon" sizes="180x180" href="/static/icons/icon-180.png"',
        'name="theme-color" content="#1f2a44"',
        "viewport-fit=cover",
        '/static/app.js',
    ]:
        assert needle in html, needle


def test_service_worker_behaviour_in_node():
    """sw.js 동작(항상 알림 표시, 외부 주소 차단, 클릭 시 /today) — Node 가 있으면 실행."""
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    script = Path(__file__).resolve().parent / "js" / "sw.test.js"
    r = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
