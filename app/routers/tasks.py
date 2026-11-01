from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Task
from ..templating import templates

router = APIRouter()


@router.get("/tasks")
def tasks_page(request: Request, edit: int | None = None, db: Session = Depends(get_db)):
    tasks = db.scalars(select(Task).order_by(Task.active.desc(), Task.subject, Task.id)).all()
    weekly_min = sum(t.duration_min * t.weekly_count for t in tasks if t.active)
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {"tasks": tasks, "edit": db.get(Task, edit) if edit else None, "weekly_min": weekly_min},
    )


@router.post("/tasks")
def save_task(
    task_id: int | None = Form(None),
    name: str = Form(...),
    subject: str = Form(""),
    duration_min: int = Form(...),
    weekly_count: int = Form(...),
    db: Session = Depends(get_db),
):
    t = db.get(Task, task_id) if task_id else None
    if t is None:
        t = Task()
        db.add(t)
    t.name = name.strip()
    t.subject = subject.strip()
    t.duration_min = max(duration_min, 1)
    t.weekly_count = min(max(weekly_count, 1), 7)
    db.commit()
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/toggle")
def toggle_task(task_id: int, db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if t:
        t.active = not t.active
        db.commit()
    return RedirectResponse("/tasks", status_code=303)
