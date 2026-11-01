import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["MAIL_BACKEND"] = "console"
os.environ["APP_PASSWORD"] = "pw"

import pytest  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture()
def db(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # outbox/ 가 임시 폴더에 생기도록
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = SessionLocal()
    seed(s)
    yield s
    s.close()
