"""로그인: 구글(OIDC) + 개발용 로그인(DEV_LOGIN).

iOS 는 Safari 와 홈 화면 앱의 쿠키 저장소가 분리되어 있고, 홈 화면 앱에서 시작한 구글 로그인이
다른 저장소(앱 안 브라우저)에서 끝날 수 있다. 그래서
- state / PKCE / nonce 는 쿠키가 아니라 login_requests 테이블에 둔다 → 콜백이 어디서 끝나든 검증 가능
- 시작한 기기에는 handoff 쿠키만 남긴다. 콜백이 같은 저장소에서 끝나면 바로 로그인,
  아니면 "앱으로 돌아가세요" 화면을 보여주고, 앱이 다시 열릴 때 /auth/handoff 로 로그인을 넘겨받는다.
"""
import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import clock
from .config import settings
from .security import limit
from .db import SessionLocal, get_db
from .models import LoginRequest, User
from .services import google_auth
from .services import legal
from .services.users import (
    CapacityFull,
    create_user,
    find_user,
    is_admin_email,
    members_count,
    normalize_email,
    record_consent,
    touch_login,
)
from .templating import templates

PUBLIC_PREFIXES = (
    "/static/",
    "/login",
    "/auth/",
    "/healthz",
    "/privacy",
    "/terms",
    "/consent",
    "/install",
    "/sw.js",
    "/manifest.webmanifest",
)
HANDOFF_COOKIE = "login_handoff"
LOGIN_TTL = timedelta(minutes=10)

router = APIRouter()


def _error_code(e: Exception) -> str:
    """사용자에게 보여 줄 짧은 오류 코드 (비밀값이 아닌 OAuth 오류 이름만)."""
    code = getattr(e, "error", None)  # authlib OAuthError: invalid_client, invalid_grant, redirect_uri_mismatch …
    if isinstance(code, str) and code.replace("_", "").isalnum():
        return code
    return type(e).__name__


def _safe_code(value: str) -> str:
    """구글이 돌려준 error 값: 영문·숫자·밑줄만, 40자까지 (로그에 다른 내용이 섞이지 않게)."""
    return re.sub(r"[^A-Za-z0-9_]", "", value)[:40] or "-"


def is_public(path: str) -> bool:
    return path.startswith(PUBLIC_PREFIXES)


def callback_url() -> str:
    return f"{settings.base_url}/auth/google/callback"


SIGNUP_KEY = "signup"  # 가입 대기 정보(서명된 세션 쿠키 안, 10분)


def full_page(request: Request):
    """정원이 가득 참: 계정을 만들지 않고 안내만. 숫자·대기 신청 없음."""
    return templates.TemplateResponse(request, "full.html", {"contact": legal.CONTACT_EMAIL}, status_code=403)


def start_signup(request: Request, email: str, name: str, sub: str | None, lr_id: int | None, same_store: bool) -> None:
    """새 사용자: DB 에 만들지 않고 서명된 세션에만 10분 동안 둔다. 동의 제출 때 만든다."""
    request.session.pop("uid", None)
    request.session[SIGNUP_KEY] = {
        "email": email, "name": name, "sub": sub, "lr": lr_id, "same": same_store,
        "exp": (clock.now() + LOGIN_TTL).timestamp(),
    }


def pending_signup(request: Request) -> dict | None:
    p = request.session.get(SIGNUP_KEY)
    if not p:
        return None
    if clock.now().timestamp() > p.get("exp", 0):
        request.session.pop(SIGNUP_KEY, None)
        return None
    return p


def finish_login(request: Request, db: Session, lr: LoginRequest | None, uid: int, same_store: bool):
    """로그인 마무리. 시작한 곳과 같은 쿠키 저장소면 바로 로그인, 아니면 '앱으로 돌아가세요' + 넘겨받기."""
    if lr is not None:
        lr.user_id = uid
        lr.completed_at = clock.now()
    if lr is None or same_store:
        if lr is not None:
            lr.consumed_at = clock.now()
        db.commit()
        _login(request, uid)
        resp = RedirectResponse("/today", status_code=303)
        resp.delete_cookie(HANDOFF_COOKIE)
        return resp
    # 다른 저장소(예: iOS 홈 화면 앱에서 시작했는데 Safari 에서 끝남) → 앱으로 돌아가 넘겨받기
    db.commit()
    request.session.clear()
    user = db.get(User, uid)
    return templates.TemplateResponse(request, "login_return.html", {"email": user.email})


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


@router.get("/auth/google/start", dependencies=[Depends(limit("login"))])
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


@router.get("/auth/google/callback", dependencies=[Depends(limit("login"))])
async def google_callback(
    request: Request, state: str = "", code: str = "", error: str = "", db: Session = Depends(get_db)
):
    lr = db.scalar(select(LoginRequest).where(LoginRequest.state == state)) if state else None
    if error or lr is None or lr.completed_at or clock.now() - lr.created_at > LOGIN_TTL:
        if error:
            print(f"[auth] google returned error={_safe_code(error)}", flush=True)
        return _login_page(
            request,
            "로그인이 취소되었거나 시간이 지났습니다. 다시 시도해 주세요." + (f" (오류 코드: {_safe_code(error)})" if error else ""),
            400,
        )
    try:
        claims = await google_auth.verify(callback_url(), code, lr.code_verifier, lr.oidc_nonce)
    except Exception as e:  # noqa: BLE001
        # 로그에는 오류 종류(코드)만 남긴다 (설명 문장·토큰·이메일 없음). 화면에도 같은 코드만
        print(f"[auth] google login verify failed: {type(e).__name__} code={_error_code(e)}", flush=True)
        return _login_page(
            request, f"구글 로그인 확인에 실패했습니다. 다시 시도해 주세요. (오류 코드: {_error_code(e)})", 400
        )

    email = claims.get("email", "")
    if not email or not claims.get("email_verified"):
        return _login_page(request, "인증된 이메일이 있는 구글 계정만 사용할 수 있습니다.", 403)
    name, sub = claims.get("name", ""), claims.get("sub")
    same_store = request.cookies.get(HANDOFF_COOKIE) == lr.handoff
    user = find_user(db, email, sub)
    if user is not None:
        # 기존 사용자: 정원이 차도 항상 로그인. 동의가 필요하면 로그인 뒤 모든 화면에서 /consent 로 안내된다
        touch_login(db, user, email, name, sub)
        return finish_login(request, db, lr, user.id, same_store)

    # 새 사용자: 정원을 먼저 확인(최종 확인은 동의 제출 때 트랜잭션 안에서) → 동의 화면. 이 브라우저에서 동의를 받는다
    if not is_admin_email(email) and members_count(db) >= settings.max_users:
        lr.completed_at = clock.now()  # 같은 로그인 요청을 다시 쓰지 못하게
        db.commit()
        return full_page(request)
    lr.completed_at = clock.now()  # 이 로그인 요청은 사용됨 (user_id 는 동의 제출 때 채운다)
    start_signup(request, email, name, sub, lr.id, same_store)
    db.commit()
    return RedirectResponse("/consent", status_code=303)


@router.get("/auth/handoff", dependencies=[Depends(limit("handoff"))])
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


@router.post("/auth/dev", dependencies=[Depends(limit("login"))])
def dev_login(request: Request, email: str = Form(...), agree: str = Form(""), db: Session = Depends(get_db)):
    """개발·테스트 전용 로그인. DEV_LOGIN=true 일 때만 동작 (운영에서는 서버가 시작을 거부).

    구글 로그인과 같은 규칙: 기존 사용자는 그대로, 새 사용자는 동의 화면을 거쳐 정원 안에서 만든다.
    agree=1 이면 (테스트 편의) 현재 버전에 동의한 것으로 바로 기록한다.
    """
    if not settings.dev_login:
        return HTMLResponse("개발용 로그인이 꺼져 있습니다.", status_code=404)
    user = find_user(db, email)
    if user is None:
        if agree:
            try:
                uid = create_user(email, consent=True)
            except CapacityFull:
                return full_page(request)
            return finish_login(request, db, None, uid, True)
        if not is_admin_email(email) and members_count(db) >= settings.max_users:
            return full_page(request)
        start_signup(request, normalize_email(email), "", None, None, True)
        return RedirectResponse("/consent", status_code=303)
    touch_login(db, user, email)
    if agree:
        record_consent(user)
        db.commit()
    return finish_login(request, db, None, user.id, True)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/healthz")
def healthz():
    """상태 확인 (Render). DB 연결만 확인하고, 설정·사용자 정보는 내보내지 않는다."""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "db": "error"}, status_code=503)
    return {"ok": True, "db": "ok"}
