from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..models import Assignment, ExtraCompletion, Task


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


def week_progress(db: Session, uid: int, week_start: date) -> int:
    """이번 주 달성 횟수 / 이번 주 목표 횟수 (%). 분모 = 활성 과제 주당 횟수 합.

    분자 = 정규 배정 달성 + 추가 달성('여유가 생기면 해볼 과제').
    추가 달성은 과제별로 '주당 횟수 - 정규 달성'까지만 센다(재배정 뒤 기록이 남아도 부풀지 않게).
    """
    weekly = dict(db.execute(select(Task.id, Task.weekly_count).where(Task.user_id == uid, Task.active.is_(True))).all())
    target = sum(weekly.values())
    if target == 0:
        return 0
    regular = dict(
        db.execute(
            select(Assignment.task_id, func.count(Assignment.id))
            .where(
                Assignment.user_id == uid,
                Assignment.week_start == week_start,
                Assignment.kind == "regular",
                Assignment.status == "done",
            )
            .group_by(Assignment.task_id)
        ).all()
    )
    extras = dict(
        db.execute(
            select(ExtraCompletion.task_id, func.count(ExtraCompletion.id))
            .where(ExtraCompletion.user_id == uid, ExtraCompletion.week_start == week_start)
            .group_by(ExtraCompletion.task_id)
        ).all()
    )
    done = sum(regular.values()) + sum(
        min(n, max(weekly[tid] - regular.get(tid, 0), 0)) for tid, n in extras.items() if tid in weekly
    )
    return min(round(done * 100 / target), 100)
