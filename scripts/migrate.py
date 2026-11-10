"""DB 마이그레이션 실행기. 서버 시작 시 자동으로 호출되며 `python -m scripts.migrate` 로 직접 실행할 수도 있다.

- 새 DB: 0001 → 최신까지 적용
- 단일 사용자 시절 DB(버전 기록 없음, settings 테이블 있음): 0001 로 표시한 뒤 업그레이드
- 적용할 마이그레이션이 있고 기존 DB 파일이 있으면, 먼저 backups/ 에 사본을 만든다
"""
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.config import settings
from app.services.backup import make_backup, sqlite_file

ROOT = Path(__file__).resolve().parent.parent


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def run(url: str | None = None) -> str:
    url = url or settings.database_url
    cfg = alembic_config(url)
    head = ScriptDirectory.from_config(cfg).get_current_head()

    path = sqlite_file(url)
    if path and os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    engine = create_engine(url)
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        current = MigrationContext.configure(conn).get_current_revision()
    engine.dispose()

    if current is None and "users" in tables:
        # 모델로 바로 만든 최신 구조 DB(테스트 등): 최신으로 표시만
        command.stamp(cfg, "head")
        return "stamped head"
    if current is None and ("settings" in tables or "tasks" in tables):
        command.stamp(cfg, "0001")  # 단일 사용자 시절 DB
        current = "0001"
    if current == head:
        return "up to date"

    backup = None
    if current is not None and path and os.path.exists(path):
        backup = make_backup(f"before-{head}", url)
        print(f"[migrate] 백업 생성: {backup}")
    command.upgrade(cfg, "head")
    print(f"[migrate] {current or '빈 DB'} → {head}")
    return f"upgraded {current} -> {head}" + (f" (backup {backup})" if backup else "")


if __name__ == "__main__":
    print(run())
