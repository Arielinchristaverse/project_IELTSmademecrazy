"""관리자 화면 숫자 (집계만, 개인 식별 정보는 넣지 않는다)."""
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import NOTIFY_DAILY, NOTIFY_REMIND, NotifyLog, PushSubscription, User
from .backup import list_backups
from .counters import get as counter
from .daily import MAX_ATTEMPTS

STATUSES = ("sent", "failed", "skipped", "pending")


def notify_summary(db: Session, d: date) -> dict:
    """오늘 발송 현황: 정기·재알림별 상태 수 + 재시도 중(실패했지만 아직 횟수가 남음)."""
    out = {}
    for kind in (NOTIFY_DAILY, NOTIFY_REMIND):
        rows = dict(
            db.execute(
                select(NotifyLog.status, func.count(NotifyLog.id))
                .where(NotifyLog.date == d, NotifyLog.kind == kind, NotifyLog.channel == "push")
                .group_by(NotifyLog.status)
            ).all()
        )
        retrying = db.scalar(
            select(func.count(NotifyLog.id)).where(
                NotifyLog.date == d, NotifyLog.kind == kind, NotifyLog.channel == "push",
                NotifyLog.status == "failed", NotifyLog.attempts < MAX_ATTEMPTS,
            )
        ) or 0
        out[kind] = {**{s: rows.get(s, 0) for s in STATUSES}, "retrying": retrying}
    return out


def build(db: Session, today: date) -> dict:
    total_users = db.scalar(select(func.count(User.id))) or 0
    members = db.scalar(select(func.count(User.id)).where(User.is_admin.is_(False))) or 0
    with_push = db.scalar(select(func.count(func.distinct(PushSubscription.user_id)))) or 0
    backups = list_backups()
    return {
        "today": today,
        "notify": notify_summary(db, today),
        "push_removed_today": counter(db, "push_removed", today),
        "push_removed_7d": sum(counter(db, "push_removed", today - timedelta(days=i)) for i in range(7)),
        "devices": db.scalar(select(func.count(PushSubscription.id))) or 0,
        "total_users": total_users,
        "members": members,
        "max_users": settings.max_users,
        "with_push": with_push,
        "push_ratio": round(with_push * 100 / total_users) if total_users else 0,
        "backup_latest": backups[0][1] if backups else None,
        "backup_latest_daily": next((t for n, t in backups if n.startswith("app-daily-")), None),
        "backup_count": len(backups),
    }
