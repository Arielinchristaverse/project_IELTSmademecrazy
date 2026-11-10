"""SQLite DB 백업. sqlite3의 온라인 백업 API를 써서 서버가 실행 중이어도 일관된 사본을 만든다."""
import os
import sqlite3
from datetime import datetime

from ..config import settings


def sqlite_file(url: str | None = None) -> str | None:
    url = url or settings.database_url
    if not url.startswith("sqlite:///"):
        return None
    return url.removeprefix("sqlite:///")


def backup_to(dest: str, url: str | None = None) -> str:
    src_path = sqlite_file(url)
    if src_path is None:
        raise RuntimeError("SQLite DB가 아니라서 이 방법으로 백업할 수 없습니다.")
    if not os.path.exists(src_path):
        raise RuntimeError(f"DB 파일이 없습니다: {src_path}")
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dest


def backup_dir(url: str | None = None) -> str:
    path = sqlite_file(url) or "./data/app.db"
    return os.path.join(os.path.dirname(os.path.abspath(path)), "backups")


def make_backup(label: str = "manual", url: str | None = None) -> str:
    name = f"app-{label}-{datetime.now():%Y%m%d-%H%M%S}.db"
    return backup_to(os.path.join(backup_dir(url), name), url)
