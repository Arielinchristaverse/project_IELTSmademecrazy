import calendar as pycal
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..db import get_db
from ..deps import current_user, safe_back
from ..models import SHIFT_CODES, SHIFT_LABELS, CalendarDay, User
from ..templating import templates

router = APIRouter()


def _parse_month(month: str | None) -> tuple[int, int]:
    if month:
        y, m = month.split("-")
        return int(y), int(m)
    t = clock.today()
    return t.year, t.month


def calendar_context(db: Session, user: User, month: str | None) -> dict:
    y, m = _parse_month(month)
    weeks = pycal.Calendar(firstweekday=0).monthdatescalendar(y, m)
    first, last = weeks[0][0], weeks[-1][-1]
    days = {
        c.date: c
        for c in db.scalars(
            select(CalendarDay).where(CalendarDay.user_id == user.id, CalendarDay.date.between(first, last))
        )
    }
    prev_m = date(y - 1, 12, 1) if m == 1 else date(y, m - 1, 1)
    next_m = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return {
        "year": y,
        "month": m,
        "weeks": weeks,
        "days": days,
        "codes": SHIFT_CODES,
        "labels": SHIFT_LABELS,
        "prev": f"{prev_m:%Y-%m}",
        "next": f"{next_m:%Y-%m}",
        "today": clock.today(),
    }


@router.get("/calendar")
def calendar_page(
    request: Request,
    month: str | None = None,
    saved: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    ctx = calendar_context(db, user, month)
    ctx["saved"] = saved
    return templates.TemplateResponse(request, "calendar.html", ctx)


@router.post("/calendar")
async def save_calendar(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    form = await request.form()
    month = form.get("month")
    y, m = _parse_month(month)
    existing = {
        c.date: c
        for c in db.scalars(
            select(CalendarDay).where(
                CalendarDay.user_id == user.id,
                CalendarDay.date.between(date(y, m, 1), date(y, m, pycal.monthrange(y, m)[1])),
            )
        )
    }
    for day in range(1, pycal.monthrange(y, m)[1] + 1):
        d = date(y, m, day)
        code = form.get(f"shift_{d.isoformat()}") or None
        excluded = form.get(f"excl_{d.isoformat()}") == "on"
        row = existing.get(d)
        if code is None and not excluded:
            if row:
                db.delete(row)
            continue
        if row is None:
            row = CalendarDay(user_id=user.id, date=d)
            db.add(row)
        row.shift_code = code if code in SHIFT_CODES else None
        row.excluded = excluded
    db.commit()
    back = safe_back(form.get("back"), "/calendar")
    return RedirectResponse(f"{back}?month={y}-{m:02d}&saved=1", status_code=303)
