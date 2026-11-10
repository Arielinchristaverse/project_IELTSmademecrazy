from . import clock

from apscheduler.schedulers.background import BackgroundScheduler

from .config import settings
from .db import SessionLocal
from .services.daily import run_due

_scheduler: BackgroundScheduler | None = None


def tick() -> None:
    now = clock.now()
    db = SessionLocal()
    try:
        for line in run_due(db, now):
            print(f"[scheduler] {now:%Y-%m-%d %H:%M} {line}")
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] error: {e}")
    finally:
        db.close()


def start() -> None:
    global _scheduler
    _scheduler = BackgroundScheduler(timezone=settings.timezone)
    _scheduler.add_job(tick, "interval", minutes=1, id="daily-mail", max_instances=1, coalesce=True)
    _scheduler.start()


def stop() -> None:
    if _scheduler:
        _scheduler.shutdown(wait=False)
