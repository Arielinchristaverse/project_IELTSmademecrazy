from datetime import date, datetime

from .config import settings


def now() -> datetime:
    return datetime.now(settings.timezone).replace(tzinfo=None)


def today() -> date:
    return now().date()
