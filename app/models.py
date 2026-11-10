"""DB 모델 (다중 사용자).

원칙
- 모든 사용자 데이터 테이블은 user_id(→ users.id, ON DELETE CASCADE)와 인덱스를 가진다.
- 유일 조건은 (user_id, …) 복합 UniqueConstraint 로 건다.
- 키는 정수 id. SQLite 전용 문법을 쓰지 않아 Postgres 로 옮기기 쉽다.
"""
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

SHIFT_CODES = ["DAY", "EVENING", "NIGHT", "OFF"]
SHIFT_LABELS = {"DAY": "데이", "EVENING": "이브닝", "NIGHT": "나이트", "OFF": "오프"}

NOTIFY_CHOICES = ("push", "email", "both")

# 온보딩 단계: 0=근무 유형, 1=과제, 2=근무 달력, 3=완료
ONBOARDING_DONE = 3


def _user_fk() -> Mapped[int]:
    return mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Seoul")  # 컬럼만 (로직은 Asia/Seoul)
    notify_channel: Mapped[str] = mapped_column(String(10), default="push")  # push | email | both
    notify_email: Mapped[str] = mapped_column(String(255), default="")  # 비면 email 사용
    sentence_cursor: Mapped[int] = mapped_column(Integer, default=0)
    onboarding_step: Mapped[int] = mapped_column(Integer, default=0)
    last_prepared_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def mail_to(self) -> str:
        return self.notify_email or self.email

    @property
    def wants_push(self) -> bool:
        return self.notify_channel in ("push", "both")

    @property
    def wants_email(self) -> bool:
        return self.notify_channel in ("email", "both")


class ShiftType(Base):
    __tablename__ = "shift_types"
    __table_args__ = (UniqueConstraint("user_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    code: Mapped[str] = mapped_column(String(10))
    label: Mapped[str] = mapped_column(String(20))
    study_minutes: Mapped[int] = mapped_column(Integer, default=0)
    send_time: Mapped[time] = mapped_column(Time, default=time(7, 0))


class CalendarDay(Base):
    __tablename__ = "calendar_days"
    __table_args__ = (UniqueConstraint("user_id", "date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    date: Mapped[date] = mapped_column(Date)
    shift_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    # 같은 사용자의 근무 유형과 (user_id, code) 로 연결
    shift: Mapped[ShiftType | None] = relationship(
        ShiftType,
        primaryjoin="and_(foreign(CalendarDay.user_id) == ShiftType.user_id, "
        "foreign(CalendarDay.shift_code) == ShiftType.code)",
        viewonly=True,
        lazy="joined",
    )


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    name: Mapped[str] = mapped_column(String(100))
    subject: Mapped[str] = mapped_column(String(50), default="")
    duration_min: Mapped[int] = mapped_column(Integer)
    weekly_count: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Week(Base):
    __tablename__ = "weeks"
    __table_args__ = (UniqueConstraint("user_id", "start_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    start_date: Mapped[date] = mapped_column(Date)
    generated_at: Mapped[datetime] = mapped_column(DateTime)
    warnings: Mapped[str] = mapped_column(Text, default="")  # 줄바꿈 구분


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        Index("ix_assignments_user_date", "user_id", "date"),
        Index("ix_assignments_user_week", "user_id", "week_start"),
        Index("ix_assignments_user_status", "user_id", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    week_start: Mapped[date] = mapped_column(Date)
    date: Mapped[date] = mapped_column(Date)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10), default="regular")  # regular | makeup
    status: Mapped[str] = mapped_column(String(10), default="scheduled")  # scheduled | done | missed
    makeup_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("assignments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    task: Mapped[Task] = relationship(lazy="joined")
    makeup_of: Mapped["Assignment | None"] = relationship(remote_side="Assignment.id", lazy="joined")


class ActionToken(Base):
    """메일 버튼용 1회용 토큰. 소유자는 assignment 를 통해 정해진다."""

    __tablename__ = "action_tokens"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(10))  # done | missed
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    assignment: Mapped[Assignment] = relationship(lazy="joined")


class Sentence(Base):
    __tablename__ = "sentences"
    __table_args__ = (Index("ix_sentences_user_position", "user_id", "position"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text)
    meaning: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)


class NotifyLog(Base):
    """사용자·날짜·채널별 발송 기록 (하루 1회 + 재시도 관리)."""

    __tablename__ = "notify_logs"
    __table_args__ = (UniqueConstraint("user_id", "date", "channel"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    date: Mapped[date] = mapped_column(Date)
    channel: Mapped[str] = mapped_column(String(10))  # push | email
    sent_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10))  # pending | sent | failed | skipped
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")


class LoginRequest(Base):
    """구글 로그인 진행 상태. state/PKCE 를 쿠키가 아닌 DB에 둬서
    iOS 홈 화면 앱과 Safari 처럼 쿠키 저장소가 달라도 로그인을 넘겨받을 수 있게 한다."""

    __tablename__ = "login_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    handoff: Mapped[str] = mapped_column(String(64), unique=True)  # 시작한 기기 쿠키에 저장되는 값
    state: Mapped[str] = mapped_column(String(64), unique=True)
    code_verifier: Mapped[str] = mapped_column(String(128))
    oidc_nonce: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
