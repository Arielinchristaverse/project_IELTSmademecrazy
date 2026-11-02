from datetime import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import SHIFT_CODES, AppSettings, ShiftType
from ..services.mailer import mail_status
from ..templating import templates

router = APIRouter()


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db), saved: int = 0):
    shifts = {s.code: s for s in db.scalars(select(ShiftType))}
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "shifts": [shifts[c] for c in SHIFT_CODES],
            "app": db.get(AppSettings, 1),
            "saved": saved,
            "mail": mail_status(),
        },
    )


@router.post("/settings")
async def save_settings(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    for code in SHIFT_CODES:
        s = db.get(ShiftType, code)
        s.study_minutes = max(int(form.get(f"{code}_minutes") or 0), 0)
        hh, mm = (form.get(f"{code}_time") or "07:00").split(":")[:2]
        s.send_time = time(int(hh), int(mm))
    db.get(AppSettings, 1).recipient_email = (form.get("recipient_email") or "").strip()
    db.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)
