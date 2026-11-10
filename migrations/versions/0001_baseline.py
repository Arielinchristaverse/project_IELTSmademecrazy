"""baseline: 단일 사용자 시절 스키마 (기존 운영 DB는 이 버전으로 표시만 한다)

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("recipient_email", sa.String(255), nullable=False),
        sa.Column("sentence_cursor", sa.Integer, nullable=False),
    )
    op.create_table(
        "shift_types",
        sa.Column("code", sa.String(10), primary_key=True),
        sa.Column("label", sa.String(20), nullable=False),
        sa.Column("study_minutes", sa.Integer, nullable=False),
        sa.Column("send_time", sa.Time, nullable=False),
    )
    op.create_table(
        "calendar_days",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("shift_code", sa.String(10), sa.ForeignKey("shift_types.code"), nullable=True),
        sa.Column("excluded", sa.Boolean, nullable=False),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("subject", sa.String(50), nullable=False),
        sa.Column("duration_min", sa.Integer, nullable=False),
        sa.Column("weekly_count", sa.Integer, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
    )
    op.create_table(
        "weeks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("start_date", sa.Date, nullable=False, unique=True),
        sa.Column("generated_at", sa.DateTime, nullable=False),
        sa.Column("warnings", sa.Text, nullable=False),
    )
    op.create_table(
        "assignments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("week_start", sa.Date, nullable=False, index=True),
        sa.Column("date", sa.Date, nullable=False, index=True),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("makeup_of_id", sa.Integer, sa.ForeignKey("assignments.id"), nullable=True),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("recorded_at", sa.DateTime, nullable=True),
    )
    op.create_table(
        "action_tokens",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column("assignment_id", sa.Integer, sa.ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("used_at", sa.DateTime, nullable=True),
    )
    op.create_table(
        "sentences",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("meaning", sa.Text, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
    )
    op.create_table(
        "mail_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("date", sa.Date, nullable=False, unique=True),
        sa.Column("sent_at", sa.DateTime, nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("error", sa.Text, nullable=False),
    )


def downgrade() -> None:
    for t in ["mail_logs", "sentences", "action_tokens", "assignments", "weeks", "tasks", "calendar_days", "shift_types", "settings"]:
        op.drop_table(t)
