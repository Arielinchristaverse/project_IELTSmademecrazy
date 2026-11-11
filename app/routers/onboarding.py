"""신규 사용자 온보딩: ① 근무 유형별 공부 시간·알림 시각 → ② 과제 → ③ 근무 달력 → 완료."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..db import get_db
from ..deps import current_user, owned
from ..models import ONBOARDING_DONE, Task, User
from ..services.allocator import generate_week, week_start_of
from ..templating import templates
from .calendar import calendar_context
from .settings import apply_shift_form, user_shifts

router = APIRouter(prefix="/onboarding")

STEPS = ["shifts", "tasks", "calendar"]
STEP_TITLES = ["근무 유형", "공부 과제", "근무 달력", "알림 켜기"]


def _render(request: Request, name: str, step: int, ctx: dict):
    ctx.update(step=step, steps=STEP_TITLES)
    return templates.TemplateResponse(request, f"onboarding/{name}.html", ctx)


def _advance(db: Session, user: User, to: int) -> None:
    user.onboarding_step = max(user.onboarding_step, to)
    db.commit()


@router.get("")
def onboarding_home(user: User = Depends(current_user)):
    if user.onboarding_step >= ONBOARDING_DONE:
        return RedirectResponse("/today", status_code=303)
    return RedirectResponse(f"/onboarding/{STEPS[user.onboarding_step]}", status_code=303)


@router.get("/shifts")
def shifts_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _render(request, "shifts", 0, {"shifts": user_shifts(db, user)})


@router.post("/shifts")
async def shifts_save(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    apply_shift_form(db, user, await request.form())
    _advance(db, user, 1)
    return RedirectResponse("/onboarding/tasks", status_code=303)


@router.get("/tasks")
def tasks_page(
    request: Request,
    edit: int | None = None,
    error: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    tasks = db.scalars(select(Task).where(Task.user_id == user.id).order_by(Task.id)).all()
    return _render(
        request,
        "tasks",
        1,
        {
            "tasks": tasks,
            "edit": owned(db, Task, edit, user) if edit else None,
            "back": "/onboarding/tasks",
            "error": error,
        },
    )


@router.post("/tasks/next")
def tasks_next(db: Session = Depends(get_db), user: User = Depends(current_user)):
    count = db.scalar(select(func.count(Task.id)).where(Task.user_id == user.id, Task.active.is_(True)))
    if not count:
        return RedirectResponse("/onboarding/tasks?error=1", status_code=303)
    _advance(db, user, 2)
    return RedirectResponse("/onboarding/calendar", status_code=303)


@router.get("/calendar")
def calendar_page(
    request: Request,
    month: str | None = None,
    saved: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    ctx = calendar_context(db, user, month)
    ctx.update(back="/onboarding/calendar", saved=saved)
    return _render(request, "calendar", 2, ctx)


@router.post("/finish")
def finish(db: Session = Depends(get_db), user: User = Depends(current_user)):
    _advance(db, user, ONBOARDING_DONE)
    today = clock.today()
    generate_week(db, user.id, week_start_of(today), today)
    return RedirectResponse("/onboarding/notify", status_code=303)


@router.get("/notify")
def notify_page(request: Request, user: User = Depends(current_user)):
    """마지막 단계(선택): 이 기기에서 알림 켜기. 권한 요청은 버튼을 눌렀을 때만 (push.js)."""
    return _render(request, "notify", 3, {"user": user})
