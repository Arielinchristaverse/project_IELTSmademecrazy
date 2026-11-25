"""가입 동의 기록 + 메일 전용 컬럼·테이블 삭제

Revision ID: 0008
Revises: 0007

동작 (전체가 하나의 트랜잭션. 시작 전 scripts/migrate.py 가 DB 백업을 만든다)
1. users 에 terms_version, privacy_version, agreed_at 추가 (모두 비어 있음 → 기존 사용자는 다음 접속 때 동의 화면)
2. users 에서 notify_channel, notify_email 삭제 (메일 기능 제거, 0007 에서 이미 push / '' 로 정리됨)
3. action_tokens(예전 메일 버튼 토큰) 테이블 삭제
SQLite 는 열 삭제 시 users 표를 새로 만들어 옮긴다(batch). 마이그레이션 연결은 외래키 검사를 켜지 않으므로
표를 옮기는 동안 다른 표의 행이 연쇄 삭제되지 않는다 (리허설 스크립트와 테스트로 행 수 보존을 확인).
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        # 안전장치: 외래키 검사가 켜진 연결이면 users 를 다시 만들 때 연쇄 삭제가 일어날 수 있으므로 중단
        if conn.exec_driver_sql("PRAGMA foreign_keys").scalar():
            raise RuntimeError("외래키 검사가 켜진 연결에서는 0008 을 실행할 수 없습니다.")
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("terms_version", sa.String(20), nullable=True))
        b.add_column(sa.Column("privacy_version", sa.String(20), nullable=True))
        b.add_column(sa.Column("agreed_at", sa.DateTime, nullable=True))
        b.drop_column("notify_channel")
        b.drop_column("notify_email")
    op.drop_table("action_tokens")


def downgrade() -> None:
    raise RuntimeError("0008 은 되돌릴 수 없습니다. 마이그레이션 전에 만든 백업 파일로 복원하세요.")
