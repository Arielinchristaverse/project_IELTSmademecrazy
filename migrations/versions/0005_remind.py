"""미달성 과제 재알림: 근무 유형별 재알림 시각, 발송 기록에 종류(정기/재알림)

Revision ID: 0005
Revises: 0004

동작 (전체가 하나의 트랜잭션. 시작 전 scripts/migrate.py 가 DB 백업을 만든다)
1. shift_types.remind_time 추가 → 기존 유형은 '알림 시각 + 6시간'으로 채움. 자정을 넘으면 비움(재알림 없음)
2. notify_logs.kind 추가 → 기존 기록은 모두 'daily'(정기)
3. 하루 1회 중복 방지 조건을 (user_id, date, channel) → (user_id, date, kind, channel) 로 교체
"""
from datetime import time

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

REMIND_AFTER_MINUTES = 6 * 60

shift_types = sa.table(
    "shift_types",
    sa.column("id", sa.Integer),
    sa.column("send_time", sa.Time),
    sa.column("remind_time", sa.Time),
)


def default_remind(send: time) -> time | None:
    total = send.hour * 60 + send.minute + REMIND_AFTER_MINUTES
    return None if total >= 24 * 60 else time(total // 60, total % 60)


def upgrade() -> None:
    conn = op.get_bind()

    # 1) 재알림 시각
    with op.batch_alter_table("shift_types") as b:
        b.add_column(sa.Column("remind_time", sa.Time, nullable=True))
    for sid, send in conn.execute(sa.select(shift_types.c.id, shift_types.c.send_time)).all():
        conn.execute(
            shift_types.update().where(shift_types.c.id == sid).values(remind_time=default_remind(send))
        )

    # 2~3) 발송 기록 종류 + 중복 방지 조건 교체
    with op.batch_alter_table("notify_logs") as b:
        b.add_column(sa.Column("kind", sa.String(10), nullable=False, server_default="daily"))
        b.drop_constraint("uq_notify_logs_user_id_date_channel", type_="unique")
        b.create_unique_constraint("uq_notify_logs_user_id_date_kind_channel", ["user_id", "date", "kind", "channel"])


def downgrade() -> None:
    raise RuntimeError("0005 는 되돌릴 수 없습니다. 마이그레이션 전에 만든 백업 파일로 복원하세요.")
