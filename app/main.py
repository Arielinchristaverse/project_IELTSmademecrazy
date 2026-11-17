from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from scripts import migrate

from . import auth, scheduler
from .config import settings
from .deps import NeedsOnboarding, NotLoggedIn
from .routers import (
    account,
    actions,
    calendar,
    install,
    onboarding,
    pwa,
    push,
    sentences,
    settings as settings_router,
    shifts,
    tasks,
    today,
    weeks,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    problems = settings.production_problems()
    if problems:
        raise RuntimeError("운영 설정 오류: " + " / ".join(problems))
    migrate.run()
    if settings.scheduler_enabled:
        scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="교대근무 공부 스케줄러", lifespan=lifespan)


@app.middleware("http")
async def static_cache_headers(request: Request, call_next):
    """정적 파일 캐시 규칙.
    - 버전이 붙은 주소(?v=…): 내용이 바뀌면 주소도 바뀌므로 1년 캐시
    - 그 밖의 CSS/JS: 매번 서버에 바뀌었는지 확인 (ETag 로 304 응답이라 가볍다)
    - 글꼴·아이콘: 하루 캐시
    헤더가 없으면 iOS Safari 가 임의로 오래 캐시해서, 배포 후에도 예전 화면이 보일 수 있다."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") and response.status_code in (200, 304):
        if "v" in request.query_params:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path.endswith((".css", ".js")):
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.middleware("http")
async def require_login(request: Request, call_next):
    if not auth.is_public(request.url.path) and not request.session.get("uid"):
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


@app.exception_handler(NotLoggedIn)
async def not_logged_in(request: Request, exc: NotLoggedIn):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(NeedsOnboarding)
async def needs_onboarding(request: Request, exc: NeedsOnboarding):
    return RedirectResponse("/onboarding", status_code=303)


# 마지막에 추가한 미들웨어가 가장 바깥에서 먼저 실행됨 → 세션이 require_login보다 먼저 준비됨
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=settings.base_url.startswith("https://"),
)

app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
app.include_router(auth.router)
app.include_router(today.router)
app.include_router(weeks.router)
app.include_router(shifts.router)
app.include_router(settings_router.router)
app.include_router(calendar.router)
app.include_router(tasks.router)
app.include_router(sentences.router)
app.include_router(actions.router)
app.include_router(onboarding.router)
app.include_router(account.router)
app.include_router(pwa.router)
app.include_router(install.router)
app.include_router(push.router)
