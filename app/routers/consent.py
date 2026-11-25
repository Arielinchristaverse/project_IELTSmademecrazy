"""가입 동의 · 재동의 · 이용약관/개인정보처리방침 문서.

- 새 사용자: 구글 인증 뒤 서명된 세션에만 정보가 있다(10분). 동의 제출 때 정원 확인 + 사용자 생성(한 트랜잭션).
- 기존 사용자: 약관·방침 버전이 바뀌었거나 동의 기록이 없으면, 로그인 상태의 모든 요청이 여기로 온다(정원 검사 없음).
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import clock
from ..auth import LOGIN_TTL, SIGNUP_KEY, _login_page, finish_login, full_page, pending_signup
from ..db import get_db
from ..models import LoginRequest, User
from ..services import legal
from ..services.users import CapacityFull, create_user, needs_consent, record_consent
from ..templating import templates

router = APIRouter()
REQUIRED = ("agree_terms", "agree_privacy", "agree_age")


def _page(request: Request, mode: str, user: User | None = None, error: str = "", status: int = 200):
    return templates.TemplateResponse(
        request,
        "consent.html",
        {"mode": mode, "user": user, "error": error, "terms": legal.terms(), "privacy": legal.privacy()},
        status_code=status,
    )


def _current(request: Request, db: Session) -> User | None:
    uid = request.session.get("uid")
    return db.get(User, uid) if uid else None


@router.get("/consent")
def consent_page(request: Request, db: Session = Depends(get_db)):
    user = _current(request, db)
    if user is not None:
        if not needs_consent(user):
            return RedirectResponse("/today", status_code=303)
        return _page(request, "update" if user.agreed_at else "existing", user)
    if pending_signup(request):
        return _page(request, "signup")
    return _login_page(request, "가입 확인 시간이 지났어요. 다시 로그인해 주세요.", 400)


@router.post("/consent")
async def consent_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    agreed = all(form.get(k) == "on" for k in REQUIRED)
    user = _current(request, db)
    if user is not None:
        if not agreed:
            return _page(request, "update" if user.agreed_at else "existing", user, "필수 항목에 모두 동의해 주세요.", 400)
        record_consent(user)
        db.commit()
        return RedirectResponse("/today", status_code=303)

    p = pending_signup(request)
    if p is None:
        return _login_page(request, "가입 확인 시간이 지났어요. 다시 로그인해 주세요.", 400)
    if not agreed:
        return _page(request, "signup", None, "필수 항목에 모두 동의해 주세요.", 400)
    try:
        uid = create_user(p["email"], p.get("name", ""), p.get("sub"), consent=True)
    except CapacityFull:
        request.session.pop(SIGNUP_KEY, None)
        return full_page(request)
    lr = db.get(LoginRequest, p["lr"]) if p.get("lr") else None
    if lr is not None and (lr.user_id or clock.now() - lr.created_at > LOGIN_TTL):
        lr = None  # 넘겨받기 시간이 지났으면 이 브라우저에서 로그인
    return finish_login(request, db, lr, uid, bool(p.get("same", True)) or lr is None)


@router.post("/consent/decline")
def consent_decline(request: Request):
    """동의하지 않음: 가입 대기 정보·로그인 상태를 지우고 로그인 화면으로 (계정은 만들지 않거나 그대로 둔다)."""
    request.session.clear()
    return _login_page(request, info="동의하지 않으면 서비스를 이용할 수 없어요. 기존 계정을 지우려면 다시 로그인한 뒤 '계정 삭제'를 이용해 주세요.")


@router.get("/terms")
def terms_page(request: Request):
    return templates.TemplateResponse(request, "doc.html", {"doc": legal.terms()})


@router.get("/privacy")
def privacy_page(request: Request):
    return templates.TemplateResponse(request, "doc.html", {"doc": legal.privacy()})
