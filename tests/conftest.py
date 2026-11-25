import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["DEV_LOGIN"] = "true"
os.environ["ADMIN_EMAIL"] = "me@example.com"
os.environ.pop("RENDER", None)

import pytest  # noqa: E402
from pywebpush import WebPushException  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base, SessionLocal, engine, signup_engine  # noqa: E402
from app.models import PushSubscription  # noqa: E402
from app.services import push  # noqa: E402
from app.services.users import get_or_create_user, record_consent  # noqa: E402
from app.clock import now as _now  # noqa: E402
from scripts.gen_vapid import generate  # noqa: E402


@pytest.fixture()
def db(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # 백업 등 상대 경로 파일이 임시 폴더에 생기도록
    engine.dispose()
    signup_engine.dispose()
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def user(db):
    """기본 테스트 사용자 = 관리자(ADMIN_EMAIL), 온보딩 완료 상태."""
    u = get_or_create_user(db, "me@example.com")
    u.onboarding_step = 3
    record_consent(u)
    db.commit()
    return u


def login(client, email="me@example.com", agree=True):
    """개발용 로그인(DEV_LOGIN)으로 해당 이메일 사용자로 들어간다.
    agree=True: 현재 약관·방침 버전에 동의한 것으로 기록 (동의 흐름 자체를 시험할 때는 False)."""
    data = {"email": email, **({"agree": "1"} if agree else {})}
    return client.post("/auth/dev", data=data, follow_redirects=False)


# ---------------------------------------------------------------- 웹 푸시 (가짜 발송)
class FakeResponse:
    def __init__(self, code):
        self.status_code = code
        self.reason = "x"
        self.text = ""


@pytest.fixture()
def pusher(monkeypatch):
    """webpush 를 가짜로 바꾼다. fail[endpoint] = 상태코드 로 실패를 흉내 낸다."""
    pub, priv = generate()
    monkeypatch.setattr(type(settings), "vapid_public_key", pub)
    monkeypatch.setattr(type(settings), "vapid_private_key", priv)
    monkeypatch.setattr(type(settings), "vapid_subject", "mailto:test@example.com")
    box = {"calls": [], "fail": {}}

    def fake_webpush(subscription_info, data, vapid_private_key, vapid_claims, **kw):
        box["calls"].append({"endpoint": subscription_info["endpoint"], "data": data, "claims": dict(vapid_claims), **kw})
        code = box["fail"].get(subscription_info["endpoint"])
        if code:
            raise WebPushException(f"Push failed: {code}", response=FakeResponse(code))

    monkeypatch.setattr(push, "webpush", fake_webpush)
    return box


def add_sub(db, user, endpoint):
    s = PushSubscription(
        user_id=user.id, endpoint=endpoint, p256dh="p" * 20, auth="a" * 16, user_agent="iPhone", created_at=_now()
    )
    db.add(s)
    db.commit()
    return s


