"""사용자 생성·조회·삭제."""
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..models import ActionToken, Assignment, User
from ..seed import seed_user


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get_or_create_user(db: Session, email: str, name: str = "", google_sub: str | None = None) -> User:
    """구글 sub → 이메일 순으로 찾고, 없으면 만든다. 관리자 여부는 ADMIN_EMAIL 과 맞춘다."""
    email = normalize_email(email)
    user = None
    if google_sub:
        user = db.scalar(select(User).where(User.google_sub == google_sub))
    if user is None:
        user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, name=name, created_at=clock.now())
        db.add(user)
        db.flush()
    if google_sub and not user.google_sub:
        user.google_sub = google_sub  # 마이그레이션으로 만든 관리자 계정을 처음 로그인할 때 연결
    if name and not user.name:
        user.name = name
    if settings.admin_email:  # 비어 있으면 건드리지 않는다 (sync_admin_flags 참고)
        user.is_admin = settings.admin_email in (email, user.email)
    user.last_login_at = clock.now()
    db.commit()
    seed_user(db, user)
    return user


def sync_admin_flags(db: Session) -> tuple[int, int] | None:
    """관리자 = ADMIN_EMAIL 계정 하나. 서버 시작 시 DB 플래그를 맞춘다: 그 계정은 켜고 나머지는 끈다.

    ADMIN_EMAIL 이 비어 있으면 실수로 모든 관리자를 잃지 않도록 아무것도 바꾸지 않는다(None 반환).
    반환: (켠 수, 끈 수)
    """
    admin = settings.admin_email
    if not admin:
        return None
    revoked = db.execute(
        update(User).where(User.is_admin.is_(True), User.email != admin).values(is_admin=False)
    ).rowcount
    granted = db.execute(
        update(User).where(User.is_admin.is_(False), User.email == admin).values(is_admin=True)
    ).rowcount
    db.commit()
    return granted, revoked


def delete_user(db: Session, user: User) -> None:
    """계정과 모든 데이터를 지운다. FK CASCADE 에 더해, 토큰처럼 간접 소유한 행도 명시적으로 지운다."""
    aid = select(Assignment.id).where(Assignment.user_id == user.id)
    db.execute(delete(ActionToken).where(ActionToken.assignment_id.in_(aid)))
    db.delete(user)
    db.commit()
