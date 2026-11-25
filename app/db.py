import os

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

# 제약조건 이름 규칙: Alembic(특히 SQLite batch 모드)과 Postgres 이전에 필요
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def make_engine(url: str, begin_immediate: bool = False):
    """begin_immediate=True: 트랜잭션을 BEGIN IMMEDIATE 로 시작하는 엔진 (SQLite 에서 쓰기 잠금을 먼저 잡음).
    가입처럼 '세고 → 만들기'를 다른 요청과 겹치지 않게 처리해야 하는 곳에서만 쓴다."""
    is_sqlite = url.startswith("sqlite")
    if url.startswith("sqlite:///"):
        folder = os.path.dirname(url.removeprefix("sqlite:///"))
        if folder:
            os.makedirs(folder, exist_ok=True)
    # timeout: 다른 연결이 쓰기 잠금을 잡고 있으면 최대 15초 기다린다
    eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15} if is_sqlite else {})
    if is_sqlite:
        # SQLite는 기본적으로 외래키(ON DELETE CASCADE)를 무시하므로 연결마다 켠다
        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
            if begin_immediate:
                dbapi_conn.isolation_level = None  # BEGIN 을 pysqlite 대신 아래에서 직접 보낸다

        if begin_immediate:
            @event.listens_for(eng, "begin")
            def _begin_immediate(conn):
                conn.exec_driver_sql("BEGIN IMMEDIATE")

    return eng


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
# 가입(정원 확인 + 사용자 생성) 전용: 동시에 들어와도 순서대로 처리된다
signup_engine = make_engine(settings.database_url, begin_immediate=True)
SignupSession = sessionmaker(bind=signup_engine, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
