from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, owned
from ..models import Task, User
from ..templating import templates

router = APIRouter()


@router.get("/tasks")
def tasks_page(
    request: Request,
    edit: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    tasks = db.scalars(
        select(Task).where(Task.user_id == user.id).order_by(Task.active.desc(), Task.subject, Task.id)
    ).all()
    weekly_min = sum(t.duration_min * t.weekly_count for t in tasks if t.active)
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {"tasks": tasks, "edit": owned(db, Task, edit, user) if edit else None, "weekly_min": weekly_min},
    )


@router.post("/tasks")
def save_task(
    task_id: int | None = Form(None),
    name: str = Form(...),
    subject: str = Form(""),
    duration_min: int = Form(...),
    weekly_count: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if task_id:
        t = owned(db, Task, task_id, user)
    else:
        t = Task(user_id=user.id)
        db.add(t)
    t.name = name.strip()
    t.subject = subject.strip()
    t.duration_min = max(duration_min, 1)
    t.weekly_count = min(max(weekly_count, 1), 7)
    db.commit()
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/toggle")
def toggle_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    t = owned(db, Task, task_id, user)
    t.active = not t.active
    db.commit()
    return RedirectResponse("/tasks", status_code=303)
