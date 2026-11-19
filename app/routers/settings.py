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
from ..deps import admin_user, current_user, safe_back
from ..models import MAX_ACTIVE_SHIFTS, NOTIFY_CHOICES, PushSubscription, ShiftType, User
from ..services import shifts as shift_svc
from ..services.backup import backup_to
from ..services.daily import send_test_mail
from ..services.mailer import MAIL_ERROR_CODES, error_code, mail_status
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
            "mail": mail_status(),
            "mail_test": request.query_params.get("mail_test"),
            "mail_codes": MAIL_ERROR_CODES,
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


@router.post("/settings/mail/test")
def settings_test_mail(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """설정 화면의 테스트 메일. 실패하면 오류 코드만 돌려준다(메시지·서버 응답·설정 값은 화면에 내지 않음)."""
    try:
        send_test_mail(db, user, clock.today())
        result = "console" if settings.mail_backend == "console" else "ok"
    except Exception as e:  # noqa: BLE001
        db.rollback()
        result = error_code(e)
        print(f"[mail:test] user={user.id} failed code={result} ({type(e).__name__})", flush=True)
    return RedirectResponse(f"/settings?mail_test={result}#mail", status_code=303)


@router.post("/settings/channel")
async def save_channel(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """알림 받는 방법만 바꾸기 (예: 알림을 거부한 경우 '메일로 받기')."""
    form = await request.form()
    if form.get("channel") in NOTIFY_CHOICES:
        user.notify_channel = form.get("channel")
        db.commit()
    return RedirectResponse(safe_back(form.get("back"), "/settings") + "?channel=1", status_code=303)


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


@router.get("/admin/backup")
def download_backup(_: User = Depends(admin_user)):
    """현재 DB의 일관된 사본을 내려받는다 (관리자 전용)."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    backup_to(tmp)
    return FileResponse(
        tmp,
        media_type="application/octet-stream",
        filename=f"afterwork-backup-{clock.now():%Y%m%d-%H%M%S}.db",
        background=BackgroundTask(os.remove, tmp),
    )
