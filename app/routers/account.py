"""계정 정보, 계정 삭제(모든 데이터), 개인정보처리방침."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import User
from ..services.users import delete_user, normalize_email
from ..templating import templates

router = APIRouter()


@router.get("/account")
def account_page(request: Request, error: int = 0, user: User = Depends(current_user)):
    return templates.TemplateResponse(request, "account.html", {"user": user, "error": error})


@router.post("/account/delete")
def account_delete(
    request: Request,
    confirm_email: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if normalize_email(confirm_email) != user.email:
        return RedirectResponse("/account?error=1", status_code=303)
    delete_user(db, user)
    request.session.clear()
    return RedirectResponse("/login?deleted=1", status_code=303)


@router.get("/privacy")
def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {})
