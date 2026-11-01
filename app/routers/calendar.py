import calendar as pycal
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..db import get_db
from ..models import SHIFT_CODES, SHIFT_LABELS, CalendarDay
from ..templating import templates

router = APIRouter()


def _parse_month(month: str | None) -> tuple[int, int]:
    if month:
        y, m = month.split("-")
        return int(y), int(m)
    t = clock.today()
    return t.year, t.month


@router.get("/calendar")
def calendar_page(request: Request, month: str | None = None, saved: int = 0, db: Session = Depends(get_db)):
    y, m = _parse_month(month)
    weeks = pycal.Calendar(firstweekday=0).monthdatescalendar(y, m)
    first, last = weeks[0][0], weeks[-1][-1]
    days = {c.date: c for c in db.scalars(select(CalendarDay).where(CalendarDay.date.between(first, last)))}
    prev_m = date(y - 1, 12, 1) if m == 1 else date(y, m - 1, 1)
    next_m = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return templates.TemplateResponse(
        request,
        "calendar.html",
        {
            "year": y,
            "month": m,
            "weeks": weeks,
            "days": days,
            "codes": SHIFT_CODES,
            "labels": SHIFT_LABELS,
            "prev": f"{prev_m:%Y-%m}",
            "next": f"{next_m:%Y-%m}",
            "today": clock.today(),
            "saved": saved,
        },
    )


@router.post("/calendar")
async def save_calendar(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    month = form.get("month")
    y, m = _parse_month(month)
    for day in range(1, pycal.monthrange(y, m)[1] + 1):
        d = date(y, m, day)
        code = form.get(f"shift_{d.isoformat()}") or None
        excluded = form.get(f"excl_{d.isoformat()}") == "on"
        row = db.get(CalendarDay, d)
        if code is None and not excluded:
            if row:
                db.delete(row)
            continue
        if row is None:
            row = CalendarDay(date=d)
            db.add(row)
        row.shift_code = code if code in SHIFT_CODES else None
        row.excluded = excluded
    db.commit()
    return RedirectResponse(f"/calendar?month={y}-{m:02d}&saved=1", status_code=303)
