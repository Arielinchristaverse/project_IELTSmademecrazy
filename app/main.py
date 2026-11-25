from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from scripts import migrate

from . import auth, scheduler, security
from .config import env_presence_line, settings
from .db import SessionLocal
from .deps import NeedsConsent, NeedsOnboarding, NotLoggedIn
from .routers import (
    account,
    admin,
    calendar,
    consent,
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
from .services.users import sync_admin_flags
from .templating import templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(env_presence_line(), flush=True)  # 설정 여부만. 값은 남기지 않는다
    problems = settings.production_problems()
    if problems:
        raise RuntimeError("운영 설정 오류: " + " / ".join(problems))
    migrate.run()
    with SessionLocal() as db:  # ADMIN_EMAIL 이 바뀌었으면 예전 관리자 권한을 뺀다 (이메일은 로그에 남기지 않음)
        r = sync_admin_flags(db)
    print("[admin] ADMIN_EMAIL 없음: 관리자 플래그 변경 안 함" if r is None else f"[admin] 관리자 동기화: 부여 {r[0]} · 해제 {r[1]}", flush=True)
    if settings.scheduler_enabled:
        scheduler.start()
    yield
    scheduler.stop()


security.install_log_filters()
# 운영에서는 자동 API 문서(/docs, /redoc, /openapi.json)를 끈다
_docs = {} if not settings.is_production else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="afterwork", lifespan=lifespan, **_docs)


async def protect(request: Request, call_next):
    """HTTPS 강제(운영) · 요청 위조 방어(상태를 바꾸는 요청은 같은 사이트에서만) · 보안 헤더."""
    if settings.is_production and request.headers.get("x-forwarded-proto", "https") == "http":
        return RedirectResponse(str(request.url.replace(scheme="https")), status_code=308)
    if request.method in security.UNSAFE and not security.same_origin(request):
        resp = (
            JSONResponse({"ok": False, "message": "잘못된 요청이에요. 앱 화면에서 다시 시도해 주세요."}, status_code=403)
            if request.url.path.startswith("/push/")
            else HTMLResponse("잘못된 요청이에요. 앱 화면을 새로 고친 뒤 다시 시도해 주세요.", status_code=403)
        )
    else:
        resp = await call_next(request)
    security.security_headers(request, resp.headers)
    return resp


@app.exception_handler(security.RateLimited)
async def rate_limited(request: Request, exc: security.RateLimited):
    if request.url.path.startswith(("/push/", "/auth/handoff")):
        return JSONResponse({"ok": False, "message": exc.detail}, status_code=429, headers={"Retry-After": "60"})
    return templates.TemplateResponse(request, "limited.html", {"message": exc.detail}, status_code=429,
                                      headers={"Retry-After": "60"})


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


@app.exception_handler(NeedsConsent)
async def needs_consent(request: Request, exc: NeedsConsent):
    return RedirectResponse("/consent", status_code=303)


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

# 가장 바깥(가장 마지막에 추가): 로그인 리디렉션·정적 파일을 포함한 모든 응답에 보안 헤더, 모든 요청에 위조 검사
app.add_middleware(BaseHTTPMiddleware, dispatch=protect)

app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
app.include_router(auth.router)
app.include_router(today.router)
app.include_router(weeks.router)
app.include_router(shifts.router)
app.include_router(settings_router.router)
app.include_router(calendar.router)
app.include_router(tasks.router)
app.include_router(sentences.router)
app.include_router(onboarding.router)
app.include_router(account.router)
app.include_router(consent.router)
app.include_router(admin.router)
app.include_router(pwa.router)
app.include_router(install.router)
app.include_router(push.router)
