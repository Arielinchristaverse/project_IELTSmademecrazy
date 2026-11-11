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
    onboarding,
    pwa,
    push,
    sentences,
    settings as settings_router,
    tasks,
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
app.include_router(weeks.router)
app.include_router(settings_router.router)
app.include_router(calendar.router)
app.include_router(tasks.router)
app.include_router(sentences.router)
app.include_router(actions.router)
app.include_router(onboarding.router)
app.include_router(account.router)
app.include_router(pwa.router)
app.include_router(push.router)
