from datetime import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import current_user
from ..models import MAX_ACTIVE_SHIFTS, PushSubscription, ShiftType, User
from ..services import shifts as shift_svc
from ..templating import templates

router = APIRouter()


def user_shifts(db: Session, user: User) -> list[ShiftType]:
    """사용 중(보관 안 한) 근무 유형, 정한 순서대로."""
    return shift_svc.list_shifts(db, user.id, include_archived=False)


def device_label(ua: str) -> str:
    ua = ua or ""
    if "iPhone" in ua:
        return "iPhone"
    if "iPad" in ua or ("Macintosh" in ua and "Mobile" in ua):
        return "iPad"
    if "Android" in ua:
        return "Android"
    for name in ("Edg", "Chrome", "Firefox", "Safari"):
        if name in ua:
            return {"Edg": "Edge"}.get(name, name) + " (PC)"
    return "기기"


def apply_shift_form(db: Session, user: User, form) -> None:
    """간단 입력 표(유형별 공부 시간·알림 시각) 저장. 폼에 없는 유형은 건드리지 않는다."""
    for s in user_shifts(db, user):
        if f"{s.code}_minutes" not in form:
            continue
        try:
            minutes = min(max(int(form.get(f"{s.code}_minutes") or 0), 0), shift_svc.MAX_STUDY_MINUTES)
            hh, mm = (form.get(f"{s.code}_time") or s.send_time.strftime("%H:%M")).split(":")[:2]
            send = time(int(hh), int(mm))
        except ValueError:
            continue
        shift_svc.set_quick(db, s, minutes, send)


@router.get("/settings")
def settings_page(
    request: Request, saved: int = 0, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "shifts": user_shifts(db, user),
            "all_shifts": shift_svc.list_shifts(db, user.id),
            "used_codes": {s.code for s in shift_svc.list_shifts(db, user.id) if shift_svc.is_used(db, s)},
            "max_shifts": MAX_ACTIVE_SHIFTS,
            "msg": request.query_params.get("msg"),
            "user": user,
            "saved": saved,
            "devices": [
                {"sub": d, "label": device_label(d.user_agent)}
                for d in db.scalars(
                    select(PushSubscription)
                    .where(PushSubscription.user_id == user.id)
                    .order_by(PushSubscription.created_at)
                )
            ],
        },
    )


@router.post("/settings")
async def save_settings(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    form = await request.form()
    apply_shift_form(db, user, form)
    db.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)


THEMES = ("auto", "light", "dark")


@router.post("/settings/theme")
async def save_theme(request: Request, _: User = Depends(current_user)):
    """화면 모드는 기기별 취향이라 계정이 아닌 이 브라우저 쿠키에 저장한다."""
    theme = (await request.form()).get("theme")
    resp = RedirectResponse("/settings#theme", status_code=303)
    if theme in ("light", "dark"):
        resp.set_cookie("theme", theme, max_age=60 * 60 * 24 * 400, samesite="lax",
                        secure=settings.base_url.startswith("https://"))
    else:
        resp.delete_cookie("theme")
    return resp
