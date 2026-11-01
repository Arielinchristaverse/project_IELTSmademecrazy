from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..models import Assignment, Task


def set_status(db: Session, a: Assignment, status: str) -> None:
    """배정 상태 변경. 보충 배정을 달성하면 원래 배정도 달성으로 바꾼다."""
    a.status = status
    a.recorded_at = None if status == "scheduled" else clock.now()
    if a.kind == "makeup" and a.makeup_of is not None:
        original = a.makeup_of
        if status == "done":
            original.status = "done"
            original.recorded_at = clock.now()
        elif original.status == "done":
            # 보충 달성을 취소하면 원래 배정은 다시 미달성(보류)
            original.status = "missed"
    db.commit()


def week_progress(db: Session, week_start: date) -> int:
    """이번 주 달성 횟수 / 이번 주 목표 횟수 (%)."""
    target = db.scalar(select(func.coalesce(func.sum(Task.weekly_count), 0)).where(Task.active.is_(True))) or 0
    if target == 0:
        return 0
    done = db.scalar(
        select(func.count(Assignment.id)).where(
            Assignment.week_start == week_start,
            Assignment.kind == "regular",
            Assignment.status == "done",
        )
    ) or 0
    return min(round(done * 100 / target), 100)
