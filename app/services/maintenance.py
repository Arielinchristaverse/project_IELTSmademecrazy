"""매일 새벽 정리 작업 (스케줄러). 로그에는 개수만 남긴다.

- DB 자동 백업 1개 (backups/app-daily-*.db)
- 30일 지난 백업 파일 삭제 (마이그레이션 직전 백업 포함)
- 로그인 진행 기록(login_requests): 1일 지나면 삭제
- 발송 기록(notify_logs): 90일 지나면 삭제
- 하루 집계(daily_counters): 30일 지나면 삭제
"""
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .. import clock
from ..models import DailyCounter, LoginRequest, NotifyLog
from .backup import backup_dir, make_backup, sqlite_file

BACKUP_KEEP_DAYS = 30
LOGIN_REQUEST_KEEP = timedelta(days=1)
NOTIFY_LOG_KEEP_DAYS = 90
COUNTER_KEEP_DAYS = 30


def prune_backups(now: datetime, url: str | None = None) -> int:
    d = Path(backup_dir(url))
    if not d.is_dir():
        return 0
    cutoff = (now - timedelta(days=BACKUP_KEEP_DAYS)).timestamp()
    removed = 0
    for p in d.glob("app-*.db"):
        if p.is_file() and p.stat().st_mtime < cutoff:
            p.unlink()
            removed += 1
    return removed


def prune_rows(db: Session, now: datetime, today: date) -> dict[str, int]:
    out = {
        "login_requests": db.execute(delete(LoginRequest).where(LoginRequest.created_at < now - LOGIN_REQUEST_KEEP)).rowcount,
        "notify_logs": db.execute(delete(NotifyLog).where(NotifyLog.date < today - timedelta(days=NOTIFY_LOG_KEEP_DAYS))).rowcount,
        "daily_counters": db.execute(delete(DailyCounter).where(DailyCounter.day < today - timedelta(days=COUNTER_KEEP_DAYS))).rowcount,
    }
    db.commit()
    return out


def run_daily(db: Session, url: str | None = None) -> str:
    now, today = clock.now(), clock.today()
    made = ""
    path = sqlite_file(url)
    if path and os.path.exists(path):
        made = os.path.basename(make_backup("daily", url))
    removed = prune_backups(now, url)
    rows = prune_rows(db, now, today)
    return f"backup={made or '없음'} 오래된 백업 삭제={removed} " + " ".join(f"{k}={v}" for k, v in rows.items())
