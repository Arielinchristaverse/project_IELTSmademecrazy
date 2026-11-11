from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..db import get_db
from ..deps import current_user, owned, safe_back
from ..models import Assignment, CalendarDay, NotifyLog, User
from ..services.allocator import (
    day_capacity,
    generate_week,
    get_week,
    is_weekend_day,
    pending_items,
    week_start_of,
)
from ..config import settings
from ..services.daily import send_test_mail
from ..services.mailer import latest_outbox_file, read_outbox
from ..services.progress import set_status, week_progress
from ..templating import templates

router = APIRouter()


@router.get("/")
def week_page(
    request: Request,
    start: str | None = None,
    msg: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    today = clock.today()
    ws = week_start_of(date.fromisoformat(start)) if start else week_start_of(today)
    week = get_week(db, user.id, ws)
    assignments = db.scalars(
        select(Assignment)
        .where(Assignment.user_id == user.id, Assignment.week_start == ws)
        .order_by(Assignment.date, Assignment.position)
    ).all()
    cal = {
        c.date: c
        for c in db.scalars(
            select(CalendarDay).where(CalendarDay.user_id == user.id, CalendarDay.date.between(ws, ws + timedelta(6)))
        )
    }
    days = []
    for i in range(7):
        d = ws + timedelta(days=i)
        c = cal.get(d)
        items = [a for a in assignments if a.date == d]
        days.append(
            {
                "date": d,
                "cal": c,
                "capacity": day_capacity(c),
                "weekend": is_weekend_day(d, c),
                "assignments": items,
                "used": sum(a.task.duration_min for a in items),
                "is_today": d == today,
            }
        )
    logs = db.scalars(
        select(NotifyLog).where(NotifyLog.user_id == user.id).order_by(NotifyLog.date.desc(), NotifyLog.id).limit(6)
    ).all()
    return templates.TemplateResponse(
        request,
        "week.html",
        {
            "ws": ws,
            "week": week,
            "days": days,
            "progress": week_progress(db, user.id, ws),
            "pending": pending_items(db, user.id),
            "prev": ws - timedelta(days=7),
            "next": ws + timedelta(days=7),
            "today": today,
            "msg": msg,
            "logs": logs,
            "preview": latest_outbox_file(user.id) if settings.mail_backend == "console" else None,
        },
    )


@router.post("/weeks/generate")
def generate(start: str = Form(...), db: Session = Depends(get_db), user: User = Depends(current_user)):
    ws = week_start_of(date.fromisoformat(start))
    generate_week(db, user.id, ws, clock.today())
    return RedirectResponse(f"/?start={ws}&msg=배정을 생성했습니다.", status_code=303)


@router.post("/mail/test")
def test_mail(day: str = Form(...), db: Session = Depends(get_db), user: User = Depends(current_user)):
    d = date.fromisoformat(day)
    try:
        result = send_test_mail(db, user, d)
    except Exception as e:  # noqa: BLE001
        result = f"발송 실패: {e}"
    return RedirectResponse(f"/?start={week_start_of(d)}&msg=테스트 메일 — {result}", status_code=303)


@router.post("/assignments/{aid}/status")
def change_status(
    aid: int,
    status: str = Form(...),
    back: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    a = owned(db, Assignment, aid, user)
    if status in ("scheduled", "done", "missed"):
        set_status(db, a, status)
    return RedirectResponse(safe_back(back, f"/?start={a.week_start}"), status_code=303)


@router.get("/mail/preview/{name}")
def mail_preview(name: str, user: User = Depends(current_user)):
    html = read_outbox(name, user.id)
    if html is None:
        return HTMLResponse("미리보기 파일이 없습니다.", status_code=404)
    return HTMLResponse(html)
