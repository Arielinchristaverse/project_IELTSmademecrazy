"""로그인: 구글(OIDC) + 개발용 로그인(DEV_LOGIN).

iOS 는 Safari 와 홈 화면 앱의 쿠키 저장소가 분리되어 있고, 홈 화면 앱에서 시작한 구글 로그인이
다른 저장소(앱 안 브라우저)에서 끝날 수 있다. 그래서
- state / PKCE / nonce 는 쿠키가 아니라 login_requests 테이블에 둔다 → 콜백이 어디서 끝나든 검증 가능
- 시작한 기기에는 handoff 쿠키만 남긴다. 콜백이 같은 저장소에서 끝나면 바로 로그인,
  아니면 "앱으로 돌아가세요" 화면을 보여주고, 앱이 다시 열릴 때 /auth/handoff 로 로그인을 넘겨받는다.
"""
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .config import settings
from .db import get_db
from .models import LoginRequest
from .services import google_auth
from .services.users import get_or_create_user, normalize_email
from .templating import templates

PUBLIC_PREFIXES = (
    "/a/",
    "/static/",
    "/login",
    "/auth/",
    "/healthz",
    "/privacy",
    "/install",
    "/sw.js",
    "/manifest.webmanifest",
)
HANDOFF_COOKIE = "login_handoff"
LOGIN_TTL = timedelta(minutes=10)

router = APIRouter()


def is_public(path: str) -> bool:
    return path.startswith(PUBLIC_PREFIXES)


def callback_url() -> str:
    return f"{settings.base_url}/auth/google/callback"


def email_allowed(email: str) -> bool:
    email = normalize_email(email)
    if not settings.allowed_emails:
        return True
    return email in settings.allowed_emails or (settings.admin_email and email == settings.admin_email)


def _login(request: Request, uid: int) -> None:
    request.session.clear()
    request.session["uid"] = uid


def _login_page(request: Request, error: str | None = None, status: int = 200, info: str | None = None):
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": error,
            "info": info,
            "google_ready": google_auth.is_configured(),
            "dev_login": settings.dev_login,
        },
        status_code=status,
    )


@router.get("/login")
def login_page(request: Request, deleted: int = 0):
    if request.session.get("uid"):
        return RedirectResponse("/today", status_code=303)
    return _login_page(request, info="계정과 모든 데이터를 삭제했습니다." if deleted else None)


@router.get("/auth/google/start")
async def google_start(db: Session = Depends(get_db)):
    if not google_auth.is_configured():
        return RedirectResponse("/login", status_code=303)
    auth = await google_auth.authorization(callback_url())
    handoff = secrets.token_urlsafe(32)
    db.add(
        LoginRequest(
            handoff=handoff,
            state=auth["state"],
            code_verifier=auth["code_verifier"],
            oidc_nonce=auth["nonce"],
            created_at=clock.now(),
        )
    )
    db.commit()
    resp = RedirectResponse(auth["url"], status_code=303)
    resp.set_cookie(
        HANDOFF_COOKIE,
        handoff,
        max_age=int(LOGIN_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.base_url.startswith("https://"),
    )
    return resp


@router.get("/auth/google/callback")
async def google_callback(
    request: Request, state: str = "", code: str = "", error: str = "", db: Session = Depends(get_db)
):
    lr = db.scalar(select(LoginRequest).where(LoginRequest.state == state)) if state else None
    if error or lr is None or lr.completed_at or clock.now() - lr.created_at > LOGIN_TTL:
        return _login_page(request, "로그인이 취소되었거나 시간이 지났습니다. 다시 시도해 주세요.", 400)
    try:
        claims = await google_auth.verify(callback_url(), code, lr.code_verifier, lr.oidc_nonce)
    except Exception:  # noqa: BLE001 — 검증 실패 내용은 사용자에게 노출하지 않는다
        return _login_page(request, "구글 로그인 확인에 실패했습니다. 다시 시도해 주세요.", 400)

    email = claims.get("email", "")
    if not email or not claims.get("email_verified"):
        return _login_page(request, "인증된 이메일이 있는 구글 계정만 사용할 수 있습니다.", 403)
    if not email_allowed(email):
        return _login_page(request, "아직 초대되지 않은 계정입니다. 관리자에게 문의해 주세요.", 403)

    user = get_or_create_user(db, email, claims.get("name", ""), claims.get("sub"))
    lr.user_id = user.id
    lr.completed_at = clock.now()

    if request.cookies.get(HANDOFF_COOKIE) == lr.handoff:
        # 시작한 곳과 같은 쿠키 저장소 → 바로 로그인
        lr.consumed_at = clock.now()
        db.commit()
        _login(request, user.id)
        resp = RedirectResponse("/today", status_code=303)
        resp.delete_cookie(HANDOFF_COOKIE)
        return resp

    # 다른 저장소(예: iOS 홈 화면 앱에서 시작했는데 Safari 에서 끝남) → 앱으로 돌아가 넘겨받기
    db.commit()
    return templates.TemplateResponse(request, "login_return.html", {"email": user.email})


@router.get("/auth/handoff")
def handoff(request: Request, redirect: int = 0, db: Session = Depends(get_db)):
    """홈 화면 앱이 다시 열렸을 때: 이 기기에서 시작한 로그인이 다른 곳에서 끝났으면 넘겨받는다."""
    code = request.cookies.get(HANDOFF_COOKIE)
    lr = db.scalar(select(LoginRequest).where(LoginRequest.handoff == code)) if code else None
    ok = bool(
        lr
        and lr.completed_at
        and lr.user_id
        and not lr.consumed_at
        and clock.now() - lr.created_at <= LOGIN_TTL
    )
    if ok:
        lr.consumed_at = clock.now()
        db.commit()
        _login(request, lr.user_id)
    if redirect:
        resp = RedirectResponse("/today" if ok else "/login", status_code=303)
    else:
        resp = JSONResponse({"ok": ok})
    if ok:
        resp.delete_cookie(HANDOFF_COOKIE)
    return resp


@router.post("/auth/dev")
def dev_login(request: Request, email: str = Form(...), db: Session = Depends(get_db)):
    """개발·테스트 전용 로그인. DEV_LOGIN=true 일 때만 동작 (운영에서는 서버가 시작을 거부)."""
    if not settings.dev_login:
        return HTMLResponse("개발용 로그인이 꺼져 있습니다.", status_code=404)
    if not email_allowed(email):
        return _login_page(request, "아직 초대되지 않은 계정입니다.", 403)
    user = get_or_create_user(db, email)
    _login(request, user.id)
    return RedirectResponse("/today", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/healthz")
def healthz():
    return {"ok": True}
