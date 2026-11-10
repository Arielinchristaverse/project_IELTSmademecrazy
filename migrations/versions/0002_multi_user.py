"""다중 사용자 전환: users 추가, 모든 데이터 테이블에 user_id, 기존 데이터는 ADMIN_EMAIL 계정으로 이전

Revision ID: 0002
Revises: 0001

동작
1. 기존 테이블을 모두 _old_* 로 이름을 바꾼다.
2. 새 구조의 테이블을 만든다 (user_id + 사용자별 유일 조건 + 인덱스).
3. 사용자가 입력한 데이터(과제·달력·배정)가 있으면 ADMIN_EMAIL 사용자를 만들고 모든 행을 그 사용자로 옮긴다.
   - 데이터가 있는데 ADMIN_EMAIL 이 없으면 실패한다 (주인 없는 데이터가 생기지 않도록).
   - 데이터가 없으면(새 DB) 예전 기본값 행은 버린다. 새 사용자는 가입 시 자기 기본값을 받는다.
4. _old_* 테이블을 지운다.

되돌리기(downgrade)는 지원하지 않는다. 시작 전에 scripts/migrate.py 가 DB 백업을 만든다.
"""
import os
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

OLD_TABLES = [
    "settings",
    "shift_types",
    "calendar_days",
    "tasks",
    "weeks",
    "assignments",
    "action_tokens",
    "sentences",
    "mail_logs",
]


def _user_fk():
    return sa.ForeignKey("users.id", ondelete="CASCADE")


def _create_new_tables() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("google_sub", sa.String(255), nullable=True, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("notify_channel", sa.String(10), nullable=False),
        sa.Column("notify_email", sa.String(255), nullable=False),
        sa.Column("sentence_cursor", sa.Integer, nullable=False),
        sa.Column("onboarding_step", sa.Integer, nullable=False),
        sa.Column("last_prepared_date", sa.Date, nullable=True),
        sa.Column("is_admin", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("last_login_at", sa.DateTime, nullable=True),
    )
    op.create_table(
        "shift_types",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, _user_fk(), nullable=False, index=True),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("label", sa.String(20), nullable=False),
        sa.Column("study_minutes", sa.Integer, nullable=False),
        sa.Column("send_time", sa.Time, nullable=False),
        sa.UniqueConstraint("user_id", "code"),
    )
    op.create_table(
        "calendar_days",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, _user_fk(), nullable=False, index=True),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("shift_code", sa.String(10), nullable=True),
        sa.Column("excluded", sa.Boolean, nullable=False),
        sa.UniqueConstraint("user_id", "date"),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, _user_fk(), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("subject", sa.String(50), nullable=False),
        sa.Column("duration_min", sa.Integer, nullable=False),
        sa.Column("weekly_count", sa.Integer, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
    )
    op.create_table(
        "weeks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, _user_fk(), nullable=False, index=True),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("generated_at", sa.DateTime, nullable=False),
        sa.Column("warnings", sa.Text, nullable=False),
        sa.UniqueConstraint("user_id", "start_date"),
    )
    op.create_table(
        "assignments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, _user_fk(), nullable=False),
        sa.Column("week_start", sa.Date, nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column(
            "makeup_of_id",
            sa.Integer,
            sa.ForeignKey("assignments.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("recorded_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_assignments_user_date", "assignments", ["user_id", "date"])
    op.create_index("ix_assignments_user_week", "assignments", ["user_id", "week_start"])
    op.create_index("ix_assignments_user_status", "assignments", ["user_id", "status"])
    op.create_table(
        "action_tokens",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column(
            "assignment_id",
            sa.Integer,
            sa.ForeignKey("assignments.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("used_at", sa.DateTime, nullable=True),
    )
    op.create_table(
        "sentences",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, _user_fk(), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("meaning", sa.Text, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
    )
    op.create_index("ix_sentences_user_position", "sentences", ["user_id", "position"])
    op.create_table(
        "notify_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, _user_fk(), nullable=False, index=True),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("sent_at", sa.DateTime, nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("error", sa.Text, nullable=False),
        sa.UniqueConstraint("user_id", "date", "channel"),
    )
    op.create_table(
        "login_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("handoff", sa.String(64), nullable=False, unique=True),
        sa.Column("state", sa.String(64), nullable=False, unique=True),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        sa.Column("oidc_nonce", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer, _user_fk(), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("consumed_at", sa.DateTime, nullable=True),
    )


def _count(conn, table: str) -> int:
    return conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0


def upgrade() -> None:
    conn = op.get_bind()

    # 0) 아무것도 바꾸기 전에 조건 확인
    has_user_data = any(_count(conn, t) for t in ("tasks", "calendar_days", "assignments"))
    admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    if has_user_data and not admin_email:
        raise RuntimeError(
            "기존 데이터가 있어 이를 넘겨받을 관리자 이메일이 필요합니다. "
            "환경 변수 ADMIN_EMAIL 을 구글 로그인에 쓸 이메일로 설정한 뒤 다시 시작하세요."
        )

    # 1) 기존 테이블 이름 바꾸기 (예전 인덱스는 이름이 겹치지 않도록 먼저 제거)
    for idx in ("ix_assignments_week_start", "ix_assignments_date"):
        conn.execute(sa.text(f"DROP INDEX IF EXISTS {idx}"))
    for t in OLD_TABLES:
        op.rename_table(t, f"_old_{t}")

    # 2) 새 테이블
    _create_new_tables()

    # 3) 데이터 이전
    if admin_email:
        st = conn.execute(sa.text("SELECT recipient_email, sentence_cursor FROM _old_settings ORDER BY id LIMIT 1")).first()
        recipient = (st[0] if st else "") or ""
        cursor = (st[1] if st else 0) or 0
        conn.execute(
            sa.text(
                "INSERT INTO users (email, google_sub, name, timezone, notify_channel, notify_email, "
                "sentence_cursor, onboarding_step, last_prepared_date, is_admin, created_at, last_login_at) "
                "VALUES (:email, NULL, '', 'Asia/Seoul', 'both', :recipient, :cursor, :step, NULL, :admin, :now, NULL)"
            ),
            {
                "email": admin_email,
                "recipient": "" if recipient.lower() == admin_email else recipient,
                "cursor": cursor,
                "step": 3 if has_user_data else 0,
                "admin": True,
                "now": datetime.now(),
            },
        )
        uid = conn.execute(sa.text("SELECT id FROM users WHERE email = :e"), {"e": admin_email}).scalar()
        p = {"uid": uid}
        conn.execute(
            sa.text(
                "INSERT INTO shift_types (user_id, code, label, study_minutes, send_time) "
                "SELECT :uid, code, label, study_minutes, send_time FROM _old_shift_types"
            ),
            p,
        )
        conn.execute(
            sa.text(
                "INSERT INTO calendar_days (user_id, date, shift_code, excluded) "
                "SELECT :uid, date, shift_code, excluded FROM _old_calendar_days"
            ),
            p,
        )
        conn.execute(
            sa.text(
                "INSERT INTO tasks (id, user_id, name, subject, duration_min, weekly_count, active) "
                "SELECT id, :uid, name, subject, duration_min, weekly_count, active FROM _old_tasks"
            ),
            p,
        )
        conn.execute(
            sa.text(
                "INSERT INTO weeks (id, user_id, start_date, generated_at, warnings) "
                "SELECT id, :uid, start_date, generated_at, warnings FROM _old_weeks"
            ),
            p,
        )
        conn.execute(
            sa.text(
                "INSERT INTO assignments (id, user_id, week_start, date, task_id, kind, status, makeup_of_id, "
                "position, recorded_at) SELECT id, :uid, week_start, date, task_id, kind, status, makeup_of_id, "
                "position, recorded_at FROM _old_assignments"
            ),
            p,
        )
        conn.execute(
            sa.text(
                "INSERT INTO action_tokens (token, assignment_id, action, expires_at, used_at) "
                "SELECT token, assignment_id, action, expires_at, used_at FROM _old_action_tokens"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO sentences (id, user_id, text, meaning, position) "
                "SELECT id, :uid, text, meaning, position FROM _old_sentences"
            ),
            p,
        )
        conn.execute(
            sa.text(
                "INSERT INTO notify_logs (user_id, date, channel, sent_at, status, attempts, error) "
                "SELECT :uid, date, 'email', sent_at, status, attempts, error FROM _old_mail_logs"
            ),
            p,
        )

    # 4) 예전 테이블 삭제
    for t in reversed(OLD_TABLES):
        op.drop_table(f"_old_{t}")


def downgrade() -> None:
    raise RuntimeError("0002 는 되돌릴 수 없습니다. 마이그레이션 전에 만든 백업 파일로 복원하세요.")
