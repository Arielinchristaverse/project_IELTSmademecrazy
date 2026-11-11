"""요청별 로그인 사용자와 '내 데이터만' 꺼내는 헬퍼."""
from typing import TypeVar

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import ONBOARDING_DONE, User

T = TypeVar("T")


class NotLoggedIn(Exception):
    """로그인 필요 → main.py 에서 /login 으로 보낸다."""


class NeedsOnboarding(Exception):
    """온보딩을 마치지 않음 → main.py 에서 /onboarding 으로 보낸다."""


# 온보딩 중에도 쓸 수 있는 경로 (온보딩 화면이 이 경로들의 저장 기능을 재사용한다)
ONBOARDING_ALLOWED = ("/onboarding", "/tasks", "/calendar", "/account", "/push")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user is None:
        request.session.clear()
        raise NotLoggedIn()
    request.state.user = user
    if user.onboarding_step < ONBOARDING_DONE and not request.url.path.startswith(ONBOARDING_ALLOWED):
        raise NeedsOnboarding()
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="관리자만 사용할 수 있습니다.")
    return user


def owned(db: Session, model: type[T], obj_id: int, user: User) -> T:
    """id 로 꺼내되 다른 사용자 것이면 없는 것과 똑같이 404."""
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "user_id", None) != user.id:
        raise HTTPException(status_code=404, detail="찾을 수 없습니다.")
    return obj


def safe_back(back: str | None, default: str) -> str:
    """돌아갈 경로: 같은 사이트 안의 경로만 허용 (외부로 튕기는 open redirect 방지)."""
    if back and back.startswith("/") and not back.startswith("//") and "\\" not in back:
        return back
    return default
