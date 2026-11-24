"""여유가 생기면 해볼 과제 — 시간 부족으로 그 주 배정에 못 넣은 과제를 나중에 직접 달성 처리.

규칙
- 대상: 그 주 week_shortfalls(배정할 때 못 넣은 횟수). 기록·되돌리기는 '이번 주'(서버 오늘, Asia/Seoul)만.
- 한도: 남은 횟수 = 못 넣은 횟수 - 기록 수 > 0 일 때만 기록. 그날 공부 가능 시간·오늘 배정은 보지 않는다.
- 재배정으로 못 넣은 횟수가 기록보다 줄어도 기록은 지우지 않는다(over). 진척도는 progress.py 에서 과제별 주당 목표까지만 센다.
- 배정·보류·재알림·푸시 내용과 무관하다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import clock
from ..models import ExtraCompletion, Task, WeekShortfall
from .allocator import week_start_of


class ExtraRefused(Exception):
    """기록·되돌리기를 할 수 없는 경우. 메시지는 화면에 그대로 보여 준다."""


@dataclass
class ExtraItem:
    task: Task
    shortfall: int  # 못 넣은 횟수
    recorded: int  # 추가 달성 기록 수

    @property
    def remaining(self) -> int:
        return max(self.shortfall - self.recorded, 0)

    @property
    def done(self) -> bool:
        return self.remaining == 0

    @property
    def over(self) -> bool:
        """재배정으로 못 넣은 횟수가 기록보다 적어진 경우."""
        return self.recorded > self.shortfall


def extra_counts(db: Session, uid: int, ws: date) -> dict[int, int]:
    rows = db.execute(
        select(ExtraCompletion.task_id, func.count(ExtraCompletion.id))
        .where(ExtraCompletion.user_id == uid, ExtraCompletion.week_start == ws)
        .group_by(ExtraCompletion.task_id)
    ).all()
    return {tid: n for tid, n in rows}


def shortfall_counts(db: Session, uid: int, ws: date) -> dict[int, int]:
    rows = db.execute(
        select(WeekShortfall.task_id, WeekShortfall.count).where(
            WeekShortfall.user_id == uid, WeekShortfall.week_start == ws
        )
    ).all()
    return {tid: n for tid, n in rows}


def week_extras(db: Session, uid: int, ws: date) -> list[ExtraItem]:
    """그 주 리스트: 못 넣은 횟수가 있거나 기록이 있는 활성 과제. 남은 것 먼저, 완료는 아래로."""
    short = shortfall_counts(db, uid, ws)
    rec = extra_counts(db, uid, ws)
    ids = set(short) | set(rec)
    if not ids:
        return []
    tasks = db.scalars(
        select(Task).where(Task.user_id == uid, Task.id.in_(ids), Task.active.is_(True)).order_by(Task.id)
    ).all()
    items = [ExtraItem(t, short.get(t.id, 0), rec.get(t.id, 0)) for t in tasks]
    return sorted(items, key=lambda it: it.done)  # 안정 정렬: 같은 그룹 안에서는 과제 순서 유지


def editable(ws: date, today: date | None = None) -> bool:
    return ws == week_start_of(today or clock.today())


def _check_week(task: Task, ws: date, today: date) -> None:
    if not editable(ws, today):
        raise ExtraRefused("이번 주 항목만 기록하거나 되돌릴 수 있어요.")
    if not task.active:
        raise ExtraRefused("쉬고 있는 과제는 기록할 수 없어요.")


def add_extra(db: Session, task: Task, ws: date, today: date | None = None) -> ExtraItem:
    today = today or clock.today()
    _check_week(task, ws, today)
    shortfall = shortfall_counts(db, task.user_id, ws).get(task.id, 0)
    recorded = extra_counts(db, task.user_id, ws).get(task.id, 0)
    if recorded >= shortfall:
        raise ExtraRefused("못 넣은 횟수만큼 이미 기록했어요.")
    db.add(ExtraCompletion(user_id=task.user_id, task_id=task.id, week_start=ws, date=today, created_at=clock.now()))
    db.commit()
    return ExtraItem(task, shortfall, recorded + 1)


def undo_extra(db: Session, task: Task, ws: date, today: date | None = None) -> None:
    today = today or clock.today()
    _check_week(task, ws, today)
    last = db.scalar(
        select(ExtraCompletion.id)
        .where(
            ExtraCompletion.user_id == task.user_id,
            ExtraCompletion.task_id == task.id,
            ExtraCompletion.week_start == ws,
        )
        .order_by(ExtraCompletion.id.desc())
        .limit(1)
    )
    if last is None:
        raise ExtraRefused("되돌릴 기록이 없어요.")
    db.execute(delete(ExtraCompletion).where(ExtraCompletion.id == last))
    db.commit()
