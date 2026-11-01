"""규칙 기반 과제 배분.

- allocate(): DB와 무관한 순수 함수. 테스트는 여기에 집중한다.
- generate_week(): DB에서 입력을 모아 allocate()를 호출하고 결과를 저장한다.

용어
- 평일: 오프가 아니고 일요일도 아닌 날
- 주말: 근무 유형이 오프인 날 + 일요일 (보류 과제를 보충하는 날)
- 보류(pending): 미달성 처리된 정규 배정 중 아직 보충 배정(예정/달성)이 없는 것
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..models import Assignment, CalendarDay, Task, Week


# ---------------------------------------------------------------------------
# 순수 로직
# ---------------------------------------------------------------------------
@dataclass
class TaskInfo:
    id: int
    duration: int
    weekly_count: int
    subject: str = ""


@dataclass
class DaySlot:
    date: date
    capacity: int  # 이미 고정된 배정을 뺀 남은 시간(분)
    is_weekend: bool
    kept_task_ids: set[int] = field(default_factory=set)  # 이 날 이미 고정된 과제
    kept_subjects: list[str] = field(default_factory=list)


@dataclass
class PendingItem:
    assignment_id: int  # 원래(미달성) 배정 id
    task_id: int
    duration: int


@dataclass
class Placement:
    task_id: int
    kind: str  # regular | makeup
    makeup_of_id: int | None = None


def allocate(
    days: list[DaySlot],
    tasks: list[TaskInfo],
    used_counts: dict[int, int],
    prev_day_task_ids: set[int],
    pending: list[PendingItem],
) -> tuple[dict[date, list[Placement]], dict[int, int]]:
    """days(날짜순)에 과제를 배정한다.

    반환: (날짜별 배정 목록, 과제별 남은 미배정 횟수)
    """
    tasks_by_id = {t.id: t for t in tasks}
    remaining = {t.id: max(t.weekly_count - used_counts.get(t.id, 0), 0) for t in tasks}
    assigned_count = {t.id: used_counts.get(t.id, 0) for t in tasks}
    pending_queue = list(pending)
    result: dict[date, list[Placement]] = {}
    prev_ids = set(prev_day_task_ids)
    caps_left: list[int] = []
    day_task_sets: list[set[int]] = []

    for i, day in enumerate(days):
        cap = day.capacity
        today_ids = set(day.kept_task_ids)
        today_subjects = list(day.kept_subjects)
        placed: list[Placement] = []

        # 1) 주말: 보류 과제를 오래된 순으로 먼저 배정
        if day.is_weekend:
            leftover = []
            for item in pending_queue:
                if item.task_id not in today_ids and item.duration <= cap:
                    placed.append(Placement(item.task_id, "makeup", item.assignment_id))
                    cap -= item.duration
                    today_ids.add(item.task_id)
                    t = tasks_by_id.get(item.task_id)
                    if t:
                        today_subjects.append(t.subject)
                else:
                    leftover.append(item)
            pending_queue = leftover

        # 2) 정규 과제: 점수 순으로 통째로 들어가는 것만 배정 (first-fit)
        def score(t: TaskInfo):
            fit_days = sum(1 for d in days[i:] if d.capacity >= t.duration)
            urgency = remaining[t.id] / (fit_days or 1)
            # 어제 했던 과제는 피한다. 단, 오늘을 건너뛰면 남은 날에 여유가 없을 때는 허용
            fit_after = fit_days - (1 if day.capacity >= t.duration else 0)
            blocked = t.id in prev_ids and remaining[t.id] < fit_after
            ratio = assigned_count[t.id] / t.weekly_count if t.weekly_count else 1
            return (
                blocked,
                -round(urgency, 4),
                round(ratio, 4),
                today_subjects.count(t.subject),
                -t.duration,
                t.id,
            )

        candidates = sorted(
            (t for t in tasks if remaining[t.id] > 0 and t.id not in today_ids),
            key=score,
        )
        for t in candidates:
            if score(t)[0]:  # 연속 배정 회피 대상
                continue
            if t.duration <= cap:
                placed.append(Placement(t.id, "regular"))
                cap -= t.duration
                remaining[t.id] -= 1
                assigned_count[t.id] += 1
                today_ids.add(t.id)
                today_subjects.append(t.subject)

        result[day.date] = placed
        prev_ids = today_ids
        caps_left.append(cap)
        day_task_sets.append(today_ids)

    # 3) 보정: 연속 회피 때문에 남은 횟수가 있으면 남는 시간에 채운다.
    #    인접한 날에 같은 과제가 없는 날 → 여유 시간이 많은 날 순으로 고른다.
    def adjacent(i: int, tid: int) -> int:
        before = day_task_sets[i - 1] if i > 0 else set(prev_day_task_ids)
        after = day_task_sets[i + 1] if i + 1 < len(days) else set()
        return (tid in before) + (tid in after)

    for t in sorted(tasks, key=lambda t: (-remaining[t.id], -t.duration, t.id)):
        while remaining[t.id] > 0:
            options = [
                i for i in range(len(days))
                if t.id not in day_task_sets[i] and caps_left[i] >= t.duration
            ]
            if not options:
                break
            i = min(options, key=lambda i: (adjacent(i, t.id), -caps_left[i], i))
            result[days[i].date].append(Placement(t.id, "regular"))
            caps_left[i] -= t.duration
            day_task_sets[i].add(t.id)
            remaining[t.id] -= 1

    return result, remaining


# ---------------------------------------------------------------------------
# DB 연동
# ---------------------------------------------------------------------------
def week_start_of(d: date) -> date:
    return d - timedelta(days=d.weekday())  # 월요일 시작


def day_capacity(cal: CalendarDay | None) -> int:
    if cal is None or cal.excluded or cal.shift is None:
        return 0
    return cal.shift.study_minutes


def is_weekend_day(d: date, cal: CalendarDay | None) -> bool:
    return d.weekday() == 6 or (cal is not None and cal.shift_code == "OFF")


def pending_items(db: Session) -> list[Assignment]:
    """보류 목록: 미달성 정규 배정 중 예정/달성 상태의 보충 배정이 없는 것 (오래된 순)."""
    missed = db.scalars(
        select(Assignment)
        .where(Assignment.kind == "regular", Assignment.status == "missed")
        .order_by(Assignment.date, Assignment.id)
    ).all()
    covered = set(
        db.scalars(
            select(Assignment.makeup_of_id).where(
                Assignment.kind == "makeup",
                Assignment.status.in_(["scheduled", "done"]),
            )
        ).all()
    )
    return [a for a in missed if a.id not in covered and a.task.active]


def auto_miss_past(db: Session, today: date) -> int:
    """기록 없이 지난 배정은 미달성(보류) 처리."""
    rows = db.scalars(
        select(Assignment).where(Assignment.status == "scheduled", Assignment.date < today)
    ).all()
    now = clock.now()
    for a in rows:
        a.status = "missed"
        a.recorded_at = now
    db.commit()
    return len(rows)


def generate_week(db: Session, week_start: date, today: date) -> Week:
    """week_start 주의 배정을 생성한다.

    오늘 이전 날짜와 이미 기록된 배정은 그대로 두고,
    오늘 이후의 '예정' 배정만 지우고 다시 만든다.
    """
    week_end = week_start + timedelta(days=6)
    from_date = max(week_start, today)

    # 오늘 이후의 예정 배정 삭제 (토큰은 assignment 삭제 시 함께 제거)
    from ..models import ActionToken

    stale = db.scalars(
        select(Assignment).where(
            Assignment.week_start == week_start,
            Assignment.date >= from_date,
            Assignment.status == "scheduled",
        )
    ).all()
    stale_ids = [a.id for a in stale]
    if stale_ids:
        for tok in db.scalars(select(ActionToken).where(ActionToken.assignment_id.in_(stale_ids))):
            db.delete(tok)
        for a in stale:
            db.delete(a)
        db.flush()

    tasks = db.scalars(select(Task).where(Task.active.is_(True)).order_by(Task.id)).all()
    task_infos = [TaskInfo(t.id, t.duration_min, t.weekly_count, t.subject) for t in tasks]

    kept = db.scalars(select(Assignment).where(Assignment.week_start == week_start)).all()
    used_counts: dict[int, int] = {}
    for a in kept:
        if a.kind == "regular":
            used_counts[a.task_id] = used_counts.get(a.task_id, 0) + 1

    cal_map = {
        c.date: c
        for c in db.scalars(
            select(CalendarDay).where(CalendarDay.date >= from_date, CalendarDay.date <= week_end)
        )
    }
    days: list[DaySlot] = []
    d = from_date
    while d <= week_end:
        cal = cal_map.get(d)
        kept_today = [a for a in kept if a.date == d]
        used_min = sum(a.task.duration_min for a in kept_today)
        days.append(
            DaySlot(
                date=d,
                capacity=max(day_capacity(cal) - used_min, 0),
                is_weekend=is_weekend_day(d, cal),
                kept_task_ids={a.task_id for a in kept_today},
                kept_subjects=[a.task.subject for a in kept_today],
            )
        )
        d += timedelta(days=1)

    prev_ids = set(
        db.scalars(select(Assignment.task_id).where(Assignment.date == from_date - timedelta(days=1)))
    )
    # 보류 과제는 '이번 주' 주말에만 배정한다. 미래 주를 미리 만들 때 보류를 가져가 버리면
    # 이번 주 오프/일요일에 보충할 기회가 사라지기 때문. 미래 주는 그 주 주말 메일 직전에 재계산된다.
    pend = (
        [PendingItem(a.id, a.task_id, a.task.duration_min) for a in pending_items(db)]
        if week_start <= week_start_of(today)
        else []
    )

    plan, remaining = allocate(days, task_infos, used_counts, prev_ids, pend)

    for day_date, placements in plan.items():
        base_pos = len([a for a in kept if a.date == day_date])
        for pos, p in enumerate(placements, start=base_pos):
            db.add(
                Assignment(
                    week_start=week_start,
                    date=day_date,
                    task_id=p.task_id,
                    kind=p.kind,
                    makeup_of_id=p.makeup_of_id,
                    position=pos,
                )
            )

    names = {t.id: t.name for t in tasks}
    warnings = [f"{names[tid]}: 이번 주 {n}회 미배정 (공부 가능 시간 부족)" for tid, n in remaining.items() if n > 0]

    week = db.scalar(select(Week).where(Week.start_date == week_start))
    if week is None:
        week = Week(start_date=week_start, generated_at=clock.now())
        db.add(week)
    week.generated_at = clock.now()
    week.warnings = "\n".join(warnings)
    db.commit()
    return week


def ensure_week(db: Session, d: date, today: date) -> None:
    ws = week_start_of(d)
    if db.scalar(select(Week).where(Week.start_date == ws)) is None:
        generate_week(db, ws, today)
