"""여유가 생기면 해볼 과제: 시간 부족으로 못 넣은 횟수(숫자) + 추가 달성 기록

Revision ID: 0006
Revises: 0005

동작 (전체가 하나의 트랜잭션. 시작 전 scripts/migrate.py 가 DB 백업을 만든다)
1. week_shortfalls 생성: 사용자·주·과제별 못 넣은 횟수. 배정을 만들 때마다 그 주를 다시 쓴다.
2. extra_completions 생성: 추가 달성 1회 = 1행.
3. 기존 weeks.warnings 문장("과제명: 이번 주 N회 미배정 (공부 가능 시간 부족)")에서 week_shortfalls 를 채운다.
   그 사용자의 활성 과제 중 이름이 정확히 하나와 일치할 때만 넣고, 애매하면 건너뛴다(다음 재배정 때 채워짐).
기존 테이블은 바꾸지 않는다.
"""
import re

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

WARNING = re.compile(r"^(.*): 이번 주 (\d+)회 미배정 \(공부 가능 시간 부족\)$")


def _user_fk():
    return sa.ForeignKey("users.id", ondelete="CASCADE")


def _task_fk():
    return sa.ForeignKey("tasks.id", ondelete="CASCADE")


def upgrade() -> None:
    op.create_table(
        "week_shortfalls",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, _user_fk(), nullable=False, index=True),
        sa.Column("week_start", sa.Date, nullable=False),
        sa.Column("task_id", sa.Integer, _task_fk(), nullable=False, index=True),
        sa.Column("count", sa.Integer, nullable=False),
        sa.UniqueConstraint("user_id", "week_start", "task_id"),
    )
    op.create_table(
        "extra_completions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, _user_fk(), nullable=False),
        sa.Column("task_id", sa.Integer, _task_fk(), nullable=False, index=True),
        sa.Column("week_start", sa.Date, nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_extra_completions_user_week", "extra_completions", ["user_id", "week_start"])

    conn = op.get_bind()
    weeks = conn.execute(
        sa.text("SELECT user_id, start_date, warnings FROM weeks WHERE warnings IS NOT NULL AND warnings != ''")
    ).all()
    for uid, start, warnings in weeks:
        names: dict[str, list[int]] = {}
        for tid, name in conn.execute(
            sa.text("SELECT id, name FROM tasks WHERE user_id = :u AND active = 1"), {"u": uid}
        ):
            names.setdefault(name, []).append(tid)
        for line in warnings.split("\n"):
            m = WARNING.match(line.strip())
            if not m or len(names.get(m.group(1), [])) != 1 or int(m.group(2)) <= 0:
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO week_shortfalls (user_id, week_start, task_id, count) VALUES (:u, :w, :t, :c)"
                ),
                {"u": uid, "w": start, "t": names[m.group(1)][0], "c": int(m.group(2))},
            )


def downgrade() -> None:
    raise RuntimeError("0006 은 되돌릴 수 없습니다. 마이그레이션 전에 만든 백업 파일로 복원하세요.")
