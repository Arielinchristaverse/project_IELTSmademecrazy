"""afterwork 워드마크: 공통 헤더 · 접근성 · 앰버 마침표 · 큰 버전 · 크기 변수 · 글꼴 로딩."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import login

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
FONT_CSS = (ROOT / "app/static/fonts/cormorant/cormorant.css").read_text(encoding="utf-8")
TABS = ["/today", "/", "/calendar", "/settings"]
LOGO = re.compile(r'<a class="wordmark( wordmark--lg)?" href="/today" aria-label="afterwork">'
                  r'afterwork<span class="dot" aria-hidden="true">\.</span></a>')


@pytest.mark.parametrize("path", TABS)
def test_common_header_on_all_four_tabs(db, user, path):
    with TestClient(app) as c:
        login(c)
        r = c.get(path)
    assert r.status_code == 200
    html = r.text
    assert html.count('<header class="site-header">') == 1  # 공통 헤더 템플릿 하나
    logos = LOGO.findall(html)
    assert logos == [""]  # 헤더의 기본 크기 로고 1개
    assert 'class="tabbar"' in html


def test_logo_markup_accessible_and_links_to_today(db, user):
    with TestClient(app) as c:
        login(c)
        html = c.get("/today").text
    m = LOGO.search(html)
    assert m, "로고 마크업(aria-label·/today 링크·aria-hidden 마침표)이 없음"


def test_dot_uses_amber_token_class():
    assert re.search(r"\.wordmark \.dot\s*\{\s*color:\s*var\(--accent-focus\);", CSS)
    assert CSS.count("--accent-focus: #F59E0B;") == 3  # 라이트 · 다크(기기) · 다크(고정) 모두 같은 앰버


@pytest.mark.parametrize("path", ["/login", "/install"])
def test_large_logo_on_login_and_install(path):
    with TestClient(app) as c:
        html = c.get(path).text
    assert LOGO.findall(html) == [" wordmark--lg"]
    assert "site-header" not in html  # 이 화면들은 헤더 없음


def test_large_logo_on_onboarding_start(db):
    with TestClient(app) as c:
        login(c, "new@example.com")
        html = c.get("/onboarding/shifts").text
        assert LOGO.findall(html) == [" wordmark--lg"]
        assert LOGO.findall(c.get("/onboarding/tasks").text) == []  # 2단계부터는 로고 없음


def test_size_is_one_variable():
    assert len(re.findall(r"--logo-size:\s*[\d.]+px", CSS)) == 1
    rule = re.search(r"\.wordmark \{([^}]*)\}", CSS).group(1)
    assert "font-size: var(--logo-size)" in rule and "font-weight: 600" in rule
    assert "calc(var(--logo-size) * 2)" in re.search(r"\.wordmark--lg \{([^}]*)\}", CSS).group(1)
    # 워드마크 크기를 px 로 직접 적은 규칙이 없어야 한다
    for sel, body in re.findall(r"([^{}]*wordmark[^{}]*)\{([^}]*)\}", CSS):
        assert not re.search(r"font-size:\s*\d", body), sel


def test_wordmark_font_does_not_flash():
    face = re.search(r'@font-face \{ font-family: "afterwork Wordmark";([^}]*)\}', FONT_CSS).group(1)
    assert "font-display: block" in face and "font-weight: 600" in face
    src = re.search(r"url\(([^)]+)\)", face).group(1)
    assert '--font-wordmark: "afterwork Wordmark"' in CSS
    with TestClient(app) as c:
        html = c.get("/login").text
        assert c.get(src).status_code == 200
    assert f'<link rel="preload" href="{src}" as="font" type="font/woff2" crossorigin>' in html
