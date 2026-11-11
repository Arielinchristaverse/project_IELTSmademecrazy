import os
import tempfile
from datetime import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .. import clock
from ..config import settings
from ..db import get_db
from ..deps import admin_user, current_user
from ..models import NOTIFY_CHOICES, SHIFT_CODES, PushSubscription, ShiftType, User
from ..services.backup import backup_to
from ..services.mailer import mail_status
from ..templating import templates

router = APIRouter()


def user_shifts(db: Session, user: User) -> list[ShiftType]:
    shifts = {s.code: s for s in db.scalars(select(ShiftType).where(ShiftType.user_id == user.id))}
    return [shifts[c] for c in SHIFT_CODES if c in shifts]


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
    for s in user_shifts(db, user):
        s.study_minutes = max(int(form.get(f"{s.code}_minutes") or 0), 0)
        hh, mm = (form.get(f"{s.code}_time") or "07:00").split(":")[:2]
        s.send_time = time(int(hh), int(mm))


@router.get("/settings")
def settings_page(
    request: Request, saved: int = 0, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "shifts": user_shifts(db, user),
            "user": user,
            "saved": saved,
            "mail": mail_status(),
            "channels_enabled": settings.notify_channels,
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
    user.notify_email = (form.get("notify_email") or "").strip()
    channel = form.get("notify_channel")
    if channel in NOTIFY_CHOICES:
        user.notify_channel = channel
    db.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.get("/admin/backup")
def download_backup(_: User = Depends(admin_user)):
    """현재 DB의 일관된 사본을 내려받는다 (관리자 전용)."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    backup_to(tmp)
    return FileResponse(
        tmp,
        media_type="application/octet-stream",
        filename=f"study-shift-backup-{clock.now():%Y%m%d-%H%M%S}.db",
        background=BackgroundTask(os.remove, tmp),
    )
