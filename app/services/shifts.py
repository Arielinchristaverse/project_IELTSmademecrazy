"""근무 유형 관리: 검증 · 추가 · 수정 · 보관 · 삭제 · (공부 시간/쉬는 날이 바뀌면) 내일부터 재배정.

규칙
- 사용 중(보관 안 한) 유형은 사용자당 최대 MAX_ACTIVE_SHIFTS 개. 보관한 유형은 개수에 넣지 않는다.
- 이름은 사용자 안에서 중복 불가 (보관한 유형 포함, 대소문자·앞뒤 공백 무시).
- 달력에서 쓰인 유형은 삭제할 수 없고 보관만 가능하다 (DB 외래키도 막는다).
- 재알림 시각은 비워 둘 수 있고(재알림 없음), 있으면 같은 날 알림 시각보다 늦어야 한다.
  기본값은 알림 시각 + 6시간, 자정을 넘으면 비움.
- 공부 가능 시간이나 쉬는 날 여부가 바뀌면 '내일부터' 배정을 다시 계산한다.
  오늘 배정은 이미 받은 알림과 어긋나지 않게 그대로 두고, 지난 날·기록한 배정도 바뀌지 않는다.
"""
from dataclasses import dataclass
from datetime import time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..models import MAX_ACTIVE_SHIFTS, SHIFT_COLORS, SHIFT_NAME_MAX, CalendarDay, ShiftType, Week
from .allocator import generate_week, week_start_of

MAX_STUDY_MINUTES = 24 * 60
REMIND_AFTER_MINUTES = 6 * 60


def default_remind(send: time) -> time | None:
    """재알림 기본값: 알림 시각 + 6시간. 자정을 넘으면 None(재알림 없음)."""
    total = send.hour * 60 + send.minute + REMIND_AFTER_MINUTES
    return None if total >= 24 * 60 else time(total // 60, total % 60)


class ShiftError(ValueError):
    """사용자에게 그대로 보여 줄 수 있는 검증 오류."""


@dataclass
class ShiftInput:
    label: str
    study_minutes: int
    send_time: time
    remind_time: time | None
    is_rest_day: bool
    color: str | None


def _parse_time(value: str | None, field: str, required: bool) -> time | None:
    value = (value or "").strip()
    if not value:
        if required:
            raise ShiftError(f"{field}을(를) 입력해 주세요.")
        return None
    try:
        hh, mm = value.split(":")[:2]
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        raise ShiftError(f"{field} 형식이 올바르지 않습니다. (예: 07:30)") from None


def parse_form(form) -> ShiftInput:
    label = " ".join((form.get("label") or "").split())
    if not label:
        raise ShiftError("이름을 입력해 주세요.")
    if len(label) > SHIFT_NAME_MAX:
        raise ShiftError(f"이름은 {SHIFT_NAME_MAX}자 이하로 입력해 주세요.")
    try:
        minutes = int(form.get("study_minutes") or 0)
    except ValueError:
        raise ShiftError("공부 가능 시간은 숫자(분)로 입력해 주세요.") from None
    if not 0 <= minutes <= MAX_STUDY_MINUTES:
        raise ShiftError(f"공부 가능 시간은 0~{MAX_STUDY_MINUTES}분 사이로 입력해 주세요.")
    color = (form.get("color") or "").strip() or None
    if color is not None and color not in SHIFT_COLORS:
        raise ShiftError("색상을 목록에서 골라 주세요.")
    send = _parse_time(form.get("send_time"), "알림 시각", required=True)
    remind = None
    if form.get("remind_on") in ("on", "1", "true"):
        remind = _parse_time(form.get("remind_time"), "재알림 시각", required=True)
        if remind <= send:
            raise ShiftError("재알림 시각은 같은 날 알림 시각보다 늦어야 해요. (자정을 넘길 수 없어요)")
    return ShiftInput(
        label=label,
        study_minutes=minutes,
        send_time=send,
        remind_time=remind,
        is_rest_day=form.get("is_rest_day") in ("on", "1", "true"),
        color=color,
    )


def list_shifts(db: Session, uid: int, include_archived: bool = True) -> list[ShiftType]:
    q = select(ShiftType).where(ShiftType.user_id == uid)
    if not include_archived:
        q = q.where(ShiftType.archived_at.is_(None))
    return db.scalars(q.order_by(ShiftType.archived_at.is_not(None), ShiftType.position, ShiftType.id)).all()


def active_count(db: Session, uid: int) -> int:
    return db.scalar(
        select(func.count(ShiftType.id)).where(ShiftType.user_id == uid, ShiftType.archived_at.is_(None))
    ) or 0


def is_used(db: Session, shift: ShiftType) -> bool:
    return (
        db.scalar(
            select(CalendarDay.id)
            .where(CalendarDay.user_id == shift.user_id, CalendarDay.shift_code == shift.code)
            .limit(1)
        )
        is not None
    )


def _check_name(db: Session, uid: int, label: str, exclude_id: int | None = None) -> None:
    key = label.casefold()
    for s in db.scalars(select(ShiftType).where(ShiftType.user_id == uid)):
        if s.id != exclude_id and s.label.casefold() == key:
            hint = " (보관된 유형에 같은 이름이 있습니다. 보관을 해제해서 쓰거나 다른 이름을 써 주세요.)" if s.is_archived else ""
            raise ShiftError(f"'{label}' 이름의 근무 유형이 이미 있습니다.{hint}")


def _next_code(db: Session, uid: int) -> str:
    codes = set(db.scalars(select(ShiftType.code).where(ShiftType.user_id == uid)))
    n = 1
    while f"U{n}" in codes:
        n += 1
    return f"U{n}"


def recalc_from_tomorrow(db: Session, uid: int) -> None:
    """내일부터 배정 재계산. 이미 만들어진 이번 주·이후 주만 다시 만든다(없는 주는 필요할 때 생성)."""
    tomorrow = clock.today() + timedelta(days=1)
    first = week_start_of(tomorrow)
    for ws in db.scalars(
        select(Week.start_date).where(Week.user_id == uid, Week.start_date >= first).order_by(Week.start_date)
    ).all():
        generate_week(db, uid, ws, tomorrow)


def create_shift(db: Session, uid: int, data: ShiftInput) -> ShiftType:
    if active_count(db, uid) >= MAX_ACTIVE_SHIFTS:
        raise ShiftError(f"근무 유형은 {MAX_ACTIVE_SHIFTS}개까지 쓸 수 있어요. 안 쓰는 유형을 보관한 뒤 추가해 주세요.")
    _check_name(db, uid, data.label)
    pos = db.scalar(select(func.max(ShiftType.position)).where(ShiftType.user_id == uid))
    shift = ShiftType(user_id=uid, code=_next_code(db, uid), position=(pos if pos is not None else -1) + 1)
    _apply(shift, data)
    db.add(shift)
    db.commit()
    return shift


def update_shift(db: Session, shift: ShiftType, data: ShiftInput) -> bool:
    """수정. 배정을 다시 계산했으면 True."""
    _check_name(db, shift.user_id, data.label, exclude_id=shift.id)
    affects = shift.study_minutes != data.study_minutes or shift.is_rest_day != data.is_rest_day
    _apply(shift, data)
    db.commit()
    if affects and is_used(db, shift):
        recalc_from_tomorrow(db, shift.user_id)
        return True
    return False


def set_quick(db: Session, shift: ShiftType, study_minutes: int, send_time: time) -> bool:
    """온보딩의 간단 입력(공부 시간·알림 시각만). 알림 시각이 재알림 시각 이후로 바뀌면
    재알림을 '새 알림 시각 + 6시간'으로 다시 맞춘다(자정을 넘으면 끔)."""
    remind = shift.remind_time
    if remind is not None and remind <= send_time:
        remind = default_remind(send_time)
    data = ShiftInput(shift.label, study_minutes, send_time, remind, shift.is_rest_day, shift.color)
    return update_shift(db, shift, data)


def archive_shift(db: Session, shift: ShiftType) -> None:
    if shift.archived_at is None:
        shift.archived_at = clock.now()
        db.commit()


def unarchive_shift(db: Session, shift: ShiftType) -> None:
    if shift.archived_at is None:
        return
    if active_count(db, shift.user_id) >= MAX_ACTIVE_SHIFTS:
        raise ShiftError(f"사용 중인 유형이 이미 {MAX_ACTIVE_SHIFTS}개예요. 다른 유형을 보관한 뒤 다시 시도해 주세요.")
    shift.archived_at = None
    db.commit()


def delete_shift(db: Session, shift: ShiftType) -> None:
    if is_used(db, shift):
        raise ShiftError("근무 달력에서 쓰고 있는 유형은 삭제할 수 없어요. 대신 '보관'하면 선택 목록에서 숨겨집니다.")
    db.delete(shift)
    db.commit()


def _apply(shift: ShiftType, data: ShiftInput) -> None:
    shift.label = data.label
    shift.study_minutes = data.study_minutes
    shift.send_time = data.send_time
    shift.remind_time = data.remind_time
    shift.is_rest_day = data.is_rest_day
    shift.color = data.color
