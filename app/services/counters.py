"""서비스 전체 하루 집계(daily_counters). 사용자 정보는 담지 않는다."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..models import DailyCounter


def bump(db: Session, key: str, n: int = 1, day: date | None = None) -> None:
    day = day or clock.today()
    row = db.scalar(select(DailyCounter).where(DailyCounter.day == day, DailyCounter.key == key))
    if row is None:
        row = DailyCounter(day=day, key=key, count=0)
        db.add(row)
    row.count += n
    db.commit()


def get(db: Session, key: str, day: date | None = None) -> int:
    day = day or clock.today()
    return db.scalar(select(DailyCounter.count).where(DailyCounter.day == day, DailyCounter.key == key)) or 0
