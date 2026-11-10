import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["MAIL_BACKEND"] = "console"
os.environ["APP_PASSWORD"] = "pw"
os.environ["ADMIN_EMAIL"] = "me@example.com"
os.environ.pop("RENDER", None)

import pytest  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.services.users import get_or_create_user  # noqa: E402


@pytest.fixture()
def db(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # outbox/ 가 임시 폴더에 생기도록
    engine.dispose()
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def user(db):
    """기본 테스트 사용자 = 관리자(ADMIN_EMAIL). 비밀번호 로그인(1a 임시)도 이 계정으로 들어간다."""
    u = get_or_create_user(db, "me@example.com")
    u.notify_channel = "email"
    u.onboarding_step = 3
    db.commit()
    return u
