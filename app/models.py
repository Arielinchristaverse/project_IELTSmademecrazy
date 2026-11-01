from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

SHIFT_CODES = ["DAY", "EVENING", "NIGHT", "OFF"]
SHIFT_LABELS = {"DAY": "데이", "EVENING": "이브닝", "NIGHT": "나이트", "OFF": "오프"}


class AppSettings(Base):
    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    recipient_email: Mapped[str] = mapped_column(String(255), default="")
    sentence_cursor: Mapped[int] = mapped_column(Integer, default=0)


class ShiftType(Base):
    __tablename__ = "shift_types"
    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    label: Mapped[str] = mapped_column(String(20))
    study_minutes: Mapped[int] = mapped_column(Integer, default=0)
    send_time: Mapped[time] = mapped_column(Time, default=time(7, 0))


class CalendarDay(Base):
    __tablename__ = "calendar_days"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    shift_code: Mapped[str | None] = mapped_column(ForeignKey("shift_types.code"), nullable=True)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    shift: Mapped[ShiftType | None] = relationship(lazy="joined")


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    subject: Mapped[str] = mapped_column(String(50), default="")
    duration_min: Mapped[int] = mapped_column(Integer)
    weekly_count: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Week(Base):
    __tablename__ = "weeks"
    id: Mapped[int] = mapped_column(primary_key=True)
    start_date: Mapped[date] = mapped_column(Date, unique=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime)
    warnings: Mapped[str] = mapped_column(Text, default="")  # 줄바꿈 구분


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    kind: Mapped[str] = mapped_column(String(10), default="regular")  # regular | makeup
    status: Mapped[str] = mapped_column(String(10), default="scheduled")  # scheduled | done | missed
    makeup_of_id: Mapped[int | None] = mapped_column(ForeignKey("assignments.id"), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    task: Mapped[Task] = relationship(lazy="joined")
    makeup_of: Mapped["Assignment | None"] = relationship(remote_side="Assignment.id", lazy="joined")


class ActionToken(Base):
    __tablename__ = "action_tokens"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(10))  # done | missed
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    assignment: Mapped[Assignment] = relationship(lazy="joined")


class Sentence(Base):
    __tablename__ = "sentences"
    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    meaning: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)


class MailLog(Base):
    __tablename__ = "mail_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, unique=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10))  # pending | sent | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
