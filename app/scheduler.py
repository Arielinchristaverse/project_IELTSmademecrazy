from . import clock

from apscheduler.schedulers.background import BackgroundScheduler

from .config import settings
from .db import SessionLocal
from .services.daily import run_due
from .services.maintenance import run_daily

_scheduler: BackgroundScheduler | None = None


def tick() -> None:
    now = clock.now()
    db = SessionLocal()
    try:
        for line in run_due(db, now):
            print(f"[scheduler] {now:%Y-%m-%d %H:%M} {line}")
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] error: {type(e).__name__}", flush=True)
    finally:
        db.close()


def daily_maintenance() -> None:
    db = SessionLocal()
    try:
        print(f"[maintenance] {run_daily(db)}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[maintenance] error: {type(e).__name__}", flush=True)
    finally:
        db.close()


def start() -> None:
    global _scheduler
    _scheduler = BackgroundScheduler(timezone=settings.timezone)
    _scheduler.add_job(tick, "interval", minutes=1, id="notify", max_instances=1, coalesce=True)
    # 매일 새벽 04:17 (Asia/Seoul): 백업 + 오래된 기록 정리. 그 시각에 서버가 잠깐 멈춰 있었으면 1시간 안에 따라잡는다
    _scheduler.add_job(daily_maintenance, "cron", hour=4, minute=17, id="maintenance",
                       misfire_grace_time=3600, coalesce=True, max_instances=1)
    _scheduler.start()


def stop() -> None:
    if _scheduler:
        _scheduler.shutdown(wait=False)
