from alembic import context
from sqlalchemy import create_engine, event

from app.config import settings
from app.db import Base
import app.models  # noqa: F401  (모델을 메타데이터에 등록)

config = context.config
url = config.get_main_option("sqlalchemy.url") or settings.database_url
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # 마이그레이션 중에는 SQLite 외래키 검사를 켜지 않는다 (테이블을 다시 만들며 복사하기 때문)
    engine = create_engine(url)
    if url.startswith("sqlite"):
        # pysqlite 기본 동작은 DDL 직전에 자동 커밋해서, 중간에 실패하면 DB가 반쯤 바뀐 채 남는다.
        # SQLAlchemy 문서의 방법대로 BEGIN 을 직접 보내 마이그레이션 전체를 하나의 트랜잭션으로 묶는다.
        @event.listens_for(engine, "connect")
        def _no_autobegin(dbapi_conn, _):
            dbapi_conn.isolation_level = None

        @event.listens_for(engine, "begin")
        def _begin(conn):
            conn.exec_driver_sql("BEGIN")

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=url.startswith("sqlite"),
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
