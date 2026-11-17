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
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# 근무 유형은 사용자가 직접 만든다. 새 사용자에게는 seed.PRESET_SHIFTS 4개가 채워진다.
MAX_ACTIVE_SHIFTS = 8
SHIFT_NAME_MAX = 20
# 유형 색 팔레트 (라이트·다크 모두에서 보이는 중간 톤, 점·선에만 쓰고 글자색으로는 쓰지 않음)
SHIFT_COLORS = {
    "sky": "#38BDF8",
    "teal": "#14B8A6",
    "violet": "#A78BFA",
    "indigo": "#6366F1",
    "rose": "#FB7185",
    "slate": "#94A3B8",
}

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
    """사용자별 근무 유형.

    code 는 달력이 가리키는 내부 키(프리셋은 DAY/EVENING/NIGHT/OFF, 직접 만든 유형은 U1, U2 …)로
    사용자에게 보이지 않고 바뀌지 않는다. 화면에 보이는 이름은 label.
    """

    __tablename__ = "shift_types"
    __table_args__ = (UniqueConstraint("user_id", "code"), UniqueConstraint("user_id", "label"))
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    code: Mapped[str] = mapped_column(String(10))
    label: Mapped[str] = mapped_column(String(SHIFT_NAME_MAX))
    study_minutes: Mapped[int] = mapped_column(Integer, default=0)
    send_time: Mapped[time] = mapped_column(Time, default=time(7, 0))
    # 근무 시작·종료 (표시용). 종료가 시작보다 이르면 야간 근무(다음 날 끝남)이고 시작한 날짜에 귀속된다.
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    is_rest_day: Mapped[bool] = mapped_column(Boolean, default=False)  # 쉬는 날 = 주말 보충 기준
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def is_overnight(self) -> bool:
        return bool(self.start_time and self.end_time and self.end_time <= self.start_time)

    @property
    def hours_text(self) -> str:
        """예: '07:00–15:00', '22:00–07:00 (+1)'. 근무 시각이 없으면 빈 문자열."""
        if not (self.start_time and self.end_time):
            return ""
        return f"{self.start_time:%H:%M}–{self.end_time:%H:%M}" + (" (+1)" if self.is_overnight else "")

    @property
    def color_hex(self) -> str | None:
        return SHIFT_COLORS.get(self.color or "")


class CalendarDay(Base):
    __tablename__ = "calendar_days"
    __table_args__ = (
        UniqueConstraint("user_id", "date"),
        # 같은 사용자의 근무 유형만 가리킬 수 있고, 달력에서 쓰는 유형은 DB 수준에서도 삭제가 막힌다
        # (계정 삭제처럼 한 문장에서 함께 지워지는 경우는 허용되도록 NO ACTION)
        ForeignKeyConstraint(
            ["user_id", "shift_code"],
            ["shift_types.user_id", "shift_types.code"],
            name="fk_calendar_days_shift_type",
        ),
    )
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


class PushSubscription(Base):
    """웹 푸시 구독 (기기·브라우저마다 하나, 한 사용자가 여러 개 가능)."""

    __tablename__ = "push_subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    endpoint: Mapped[str] = mapped_column(String(2048), unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    user_agent: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
