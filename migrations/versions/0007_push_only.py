"""푸시 전용 전환: 메일 기능 제거에 따른 데이터 정리 + 서비스 전체 하루 집계

Revision ID: 0007
Revises: 0006

동작 (전체가 하나의 트랜잭션. 시작 전 scripts/migrate.py 가 DB 백업을 만든다)
1. 모든 사용자의 알림 수단을 'push' 로 (메일·둘 다를 골랐던 사용자 포함)
2. 쓰지 않게 된 '메일 받는 주소'(users.notify_email)를 비운다 (개인정보 최소화. 컬럼 삭제는 승인 후 별도)
3. daily_counters 생성: 날짜·항목별 서비스 전체 집계 (예: 만료되어 지운 푸시 구독 수). 사용자 정보 없음
action_tokens(예전 메일 버튼) 테이블은 그대로 둔다 (삭제는 승인 후 별도).
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE users SET notify_channel = 'push' WHERE notify_channel != 'push'")
    op.execute("UPDATE users SET notify_email = '' WHERE notify_email != ''")
    op.create_table(
        "daily_counters",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("count", sa.Integer, nullable=False),
        sa.UniqueConstraint("day", "key"),
    )


def downgrade() -> None:
    raise RuntimeError("0007 은 되돌릴 수 없습니다. 마이그레이션 전에 만든 백업 파일로 복원하세요.")
