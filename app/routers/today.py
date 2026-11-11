"""'오늘' 화면: 알림을 누르면 열리는 화면. 메일과 같은 내용(build_today_context)을 보여주고,
로그인된 상태에서 바로 달성/미달성을 기록한다 (기록은 /assignments/{id}/status 재사용)."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .. import clock
from ..db import get_db
from ..deps import current_user
from ..models import User
from ..services.allocator import day_capacity, get_calendar_day
from ..services.daily import build_today_context, prepare_day
from ..templating import templates

router = APIRouter()


@router.get("/today")
def today_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    d = clock.today()
    assignments = prepare_day(db, user, d)
    ctx = build_today_context(db, user, d, assignments)
    cal = get_calendar_day(db, user.id, d)
    ctx.update(
        cal=cal,
        capacity=day_capacity(cal),
        excluded=bool(cal and cal.excluded),
        no_shift=cal is None or cal.shift is None,
        done_count=sum(1 for a in assignments if a.status == "done"),
    )
    return templates.TemplateResponse(request, "today.html", ctx)
