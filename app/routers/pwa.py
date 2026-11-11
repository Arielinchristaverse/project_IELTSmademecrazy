"""PWA: 매니페스트와 서비스 워커. 둘 다 사이트 최상위(/)에서 제공해야 앱 전체를 범위로 잡을 수 있다."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()

STATIC = Path(__file__).resolve().parent.parent / "static"
CANVAS = "#FAFAFA"  # 라이트 모드 바탕 (브리프 --bg-canvas)

MANIFEST = {
    "id": "/",
    "name": "afterwork — 교대근무 맞춤 학습 플래너",
    "short_name": "afterwork",
    "description": "오늘의 작은 실행이, 내 삶의 다음 페이지를 만듭니다. 근무표에 맞춰 매일 오늘의 과제를 알려 드립니다.",
    "lang": "ko",
    "start_url": "/today?source=pwa",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": CANVAS,
    "theme_color": CANVAS,
    "icons": [
        {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}


@router.get("/manifest.webmanifest")
def manifest():
    return JSONResponse(MANIFEST, media_type="application/manifest+json")


@router.get("/sw.js")
def service_worker():
    return FileResponse(
        STATIC / "sw.js",
        media_type="text/javascript",
        headers={
            "Cache-Control": "no-cache",  # 서비스 워커 갱신이 바로 반영되도록
            "Service-Worker-Allowed": "/",
        },
    )
