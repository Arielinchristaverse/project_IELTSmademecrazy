"""사용자 정의 근무 유형: 쉬는 날 · 색 · 순서 · 보관, 이름 중복 방지, 달력→유형 외래키

Revision ID: 0004
Revises: 0003

동작 (전체가 하나의 트랜잭션. 시작 전 scripts/migrate.py 가 DB 백업을 만든다)
1. shift_types 에 칸 추가: is_rest_day, color, position, archived_at
2. 기존 프리셋 4개 값 채우기: OFF → 쉬는 날, 순서·색
3. 달력이 가리키는데 유형 표에 없는 코드가 있으면, 기록을 지우지 않도록 '보관된 유형'으로 만들어 보존
4. 같은 사용자 안에서 이름이 겹치면 뒤에 번호를 붙인 뒤, (user_id, label) 중복 방지 조건 추가
5. calendar_days (user_id, shift_code) → shift_types (user_id, code) 외래키 추가
"""
from datetime import datetime, time

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

PRESETS = {
    # code: (position, is_rest_day, color)
    "DAY": (0, False, "sky"),
    "EVENING": (1, False, "violet"),
    "NIGHT": (2, False, "indigo"),
    "OFF": (3, True, "teal"),
}

shift_types = sa.table(
    "shift_types",
    sa.column("id", sa.Integer),
    sa.column("user_id", sa.Integer),
    sa.column("code", sa.String),
    sa.column("label", sa.String),
    sa.column("study_minutes", sa.Integer),
    sa.column("send_time", sa.Time),
    sa.column("is_rest_day", sa.Boolean),
    sa.column("color", sa.String),
    sa.column("position", sa.Integer),
    sa.column("archived_at", sa.DateTime),
)


def upgrade() -> None:
    conn = op.get_bind()

    # 1) 칸 추가
    with op.batch_alter_table("shift_types") as b:
        b.add_column(sa.Column("is_rest_day", sa.Boolean, nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("color", sa.String(16), nullable=True))
        b.add_column(sa.Column("position", sa.Integer, nullable=False, server_default="0"))
        b.add_column(sa.Column("archived_at", sa.DateTime, nullable=True))

    # 2) 기존 프리셋 값 채우기
    for code, (pos, rest, color) in PRESETS.items():
        conn.execute(
            shift_types.update().where(shift_types.c.code == code).values(position=pos, is_rest_day=rest, color=color)
        )
    # 프리셋이 아닌 코드는 뒤쪽 순서로
    conn.execute(shift_types.update().where(shift_types.c.code.not_in(list(PRESETS))).values(position=10))

    # 3) 달력이 가리키는데 유형이 없는 코드 → 보관된 유형으로 보존
    orphans = conn.execute(
        sa.text(
            "SELECT DISTINCT cd.user_id, cd.shift_code FROM calendar_days cd "
            "LEFT JOIN shift_types st ON st.user_id = cd.user_id AND st.code = cd.shift_code "
            "WHERE cd.shift_code IS NOT NULL AND st.id IS NULL"
        )
    ).all()
    for uid, code in orphans:
        conn.execute(
            shift_types.insert().values(
                user_id=uid, code=code, label=code, study_minutes=0, send_time=time(7, 0),
                is_rest_day=False, color=None, position=99,
                archived_at=datetime.now(),
            )
        )

    # 4) 같은 사용자 안 이름 중복 정리 (지금 구조에서는 생길 수 없지만 안전장치)
    rows = conn.execute(sa.text("SELECT id, user_id, label FROM shift_types ORDER BY user_id, position, id")).all()
    seen: dict[tuple[int, str], int] = {}
    for sid, uid, label in rows:
        key = (uid, label.casefold())
        if key in seen:
            seen[key] += 1
            new = f"{label[:16]} ({seen[key]})"
            conn.execute(shift_types.update().where(shift_types.c.id == sid).values(label=new))
        else:
            seen[key] = 1
    with op.batch_alter_table("shift_types") as b:
        b.create_unique_constraint("uq_shift_types_user_id_label", ["user_id", "label"])

    # 5) 달력 → 근무 유형 외래키 (같은 사용자의 유형만, 쓰이는 유형은 삭제 불가)
    with op.batch_alter_table("calendar_days") as b:
        b.create_foreign_key(
            "fk_calendar_days_shift_type", "shift_types", ["user_id", "shift_code"], ["user_id", "code"]
        )


def downgrade() -> None:
    raise RuntimeError("0004 는 되돌릴 수 없습니다. 마이그레이션 전에 만든 백업 파일로 복원하세요.")
