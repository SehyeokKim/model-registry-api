"""add organizations analytics_consent

고객 데이터를 내부 품질 분석에 써도 된다는 조직의 계약상 동의 여부를 기록한다.
분석 레이크의 조직 행 필터가 이 값을 쓴다. 기본값은 동의하지 않음(false)이다.
PostgreSQL 11 이상에서 상수 기본값을 가진 컬럼 추가는 테이블을 다시 쓰지 않는다.

Revision ID: d6eaa36b39e1
Revises: 7bf28165b88b
Create Date: 2026-10-07 11:03:40.854002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6eaa36b39e1"
down_revision: str | Sequence[str] | None = "7bf28165b88b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "analytics_consent", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "analytics_consent")
