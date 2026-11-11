"""설치 안내: iOS Safari 에서는 '홈 화면에 추가 → 홈 화면 앱에서 로그인 → 알림 허용' 순서를 먼저 안내한다.

iOS 는 Safari 와 홈 화면 앱의 쿠키 저장소가 분리되어 있어서, Safari 에서 로그인해도 홈 화면 앱은 로그인되지 않는다.
그래서 로그인 화면(login.js)이 'iOS + 아직 홈 화면 앱이 아님'을 감지하면 이 화면으로 먼저 보낸다.
"""
from fastapi import APIRouter, Request

from ..templating import templates

router = APIRouter()

IN_APP_BROWSERS = ("KAKAOTALK", "Instagram", "FBAN", "FBAV", "NAVER", "Line/", "DaumApps", "everytimeApp")


def browser_kind(ua: str) -> str:
    ua = ua or ""
    if any(k in ua for k in IN_APP_BROWSERS):
        return "inapp"  # 카카오톡·인스타그램 등 앱 안 브라우저 → 홈 화면에 추가 불가, Safari 로 열어야 함
    if "CriOS" in ua or "FxiOS" in ua or "EdgiOS" in ua:
        return "ios-other"  # iOS 크롬·파이어폭스 등 (iOS 16.4+ 는 공유 메뉴에서 홈 화면에 추가 가능)
    return "safari"


@router.get("/install")
def install(request: Request):
    return templates.TemplateResponse(
        request, "install.html", {"kind": browser_kind(request.headers.get("user-agent", ""))}
    )
