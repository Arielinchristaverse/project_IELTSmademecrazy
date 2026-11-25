"""사용자 생성·조회·삭제 · 가입 정원 · 동의 기록."""
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..db import SessionLocal, SignupSession
from ..models import User
from ..seed import seed_user
from . import legal


def normalize_email(email: str) -> str:
    return email.strip().lower()


class CapacityFull(Exception):
    """가입 정원이 가득 참 (계정을 만들지 않았다)."""


def is_admin_email(email: str) -> bool:
    return bool(settings.admin_email) and normalize_email(email) == settings.admin_email


def find_user(db: Session, email: str, google_sub: str | None = None) -> User | None:
    """구글 sub → 이메일 순으로 찾는다 (만들지 않는다)."""
    user = db.scalar(select(User).where(User.google_sub == google_sub)) if google_sub else None
    return user or db.scalar(select(User).where(User.email == normalize_email(email)))


def touch_login(db: Session, user: User, email: str, name: str = "", google_sub: str | None = None) -> User:
    """기존 사용자 로그인: 구글 계정 연결, 이름 채우기, 관리자 여부 맞추기, 최근 로그인 시각. 정원 검사 없음."""
    if google_sub and not user.google_sub:
        user.google_sub = google_sub  # 마이그레이션으로 만든 관리자 계정을 처음 로그인할 때 연결
    if name and not user.name:
        user.name = name
    if settings.admin_email:
        user.is_admin = settings.admin_email in (normalize_email(email), user.email)
    user.last_login_at = clock.now()
    db.commit()
    return user


def members_count(db: Session) -> int:
    """정원에 들어가는 가입자 수 (관리자 제외)."""
    return db.scalar(select(func.count(User.id)).where(User.is_admin.is_(False))) or 0


def seats_left(db: Session) -> int:
    return max(settings.max_users - members_count(db), 0)


def needs_consent(user: User) -> bool:
    """현재 약관·방침 버전에 동의하지 않았으면 True (처음이든 버전이 올라갔든)."""
    return (
        user.agreed_at is None
        or user.terms_version != legal.terms().version
        or user.privacy_version != legal.privacy().version
    )


def record_consent(user: User) -> None:
    user.terms_version = legal.terms().version
    user.privacy_version = legal.privacy().version
    user.agreed_at = clock.now()


def create_user(email: str, name: str = "", google_sub: str | None = None, consent: bool = False) -> int:
    """새 사용자를 만든다. 정원 확인과 생성을 한 트랜잭션(BEGIN IMMEDIATE)에서 처리해 동시에 가입해도 넘치지 않는다.

    ADMIN_EMAIL 계정은 정원에서 제외. 같은 사람이 동시에 두 번 제출하면 기존 계정 id 를 돌려준다.
    가득 차면 CapacityFull. 반환: 사용자 id.
    """
    email = normalize_email(email)
    admin = is_admin_email(email)
    with SignupSession() as s:  # 첫 쿼리에서 BEGIN IMMEDIATE → 다른 가입은 커밋까지 기다린다
        existing = find_user(s, email, google_sub)
        if existing is not None:
            return existing.id
        if not admin and members_count(s) >= settings.max_users:
            s.rollback()
            raise CapacityFull()
        user = User(email=email, name=name, google_sub=google_sub, created_at=clock.now(),
                    last_login_at=clock.now(), is_admin=admin)
        if consent:
            record_consent(user)
        s.add(user)
        s.commit()
        uid = user.id
    with SessionLocal() as db:  # 기본 근무 유형·영어 문장 (정원 트랜잭션 밖에서)
        seed_user(db, db.get(User, uid))
    return uid


def get_or_create_user(db: Session, email: str, name: str = "", google_sub: str | None = None) -> User:
    """(테스트·관리용) 구글 sub → 이메일 순으로 찾고, 없으면 정원·동의 확인 없이 만든다.
    실제 가입은 create_user(정원·동의 포함)를 쓴다."""
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
    """계정과 모든 데이터를 지운다 (모든 사용자 데이터 표는 user_id 외래키 ON DELETE CASCADE)."""
    db.delete(user)
    db.commit()
