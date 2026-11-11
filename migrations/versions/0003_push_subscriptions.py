"""웹 푸시 구독 테이블

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("endpoint", sa.String(2048), nullable=False, unique=True),
        sa.Column("p256dh", sa.String(255), nullable=False),
        sa.Column("auth", sa.String(255), nullable=False),
        sa.Column("user_agent", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("last_success_at", sa.DateTime, nullable=True),
        sa.Column("last_error", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("push_subscriptions")
