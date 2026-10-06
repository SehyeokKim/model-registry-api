"""create initial schema

organizations, users, models, model_versions 테이블을 만든다.
enum 컬럼은 varchar + CHECK 제약으로 저장한다 (ck_<table>_<column>).

Revision ID: 7bf28165b88b
Revises:
Create Date: 2026-10-06 17:13:10.989214
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7bf28165b88b"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_pk() -> sa.Column:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "organizations",
        _uuid_pk(),
        sa.Column("name", sa.String(length=100), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
    )

    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "role IN ('CUSTOMER', 'ML_ENGINEER', 'ADMIN')", name=op.f("ck_users_role")
        ),
        sa.CheckConstraint(
            "(role = 'CUSTOMER') = (organization_id IS NOT NULL)",
            name=op.f("ck_users_customer_has_organization"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_users_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_organization_id"), "users", ["organization_id"])

    op.create_table(
        "models",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("task_type", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "task_type IN ('CLASSIFICATION', 'REGRESSION')", name=op.f("ck_models_task_type")
        ),
        sa.CheckConstraint(
            "name IS NFC NORMALIZED AND name = btrim(name) AND char_length(name) >= 1",
            name=op.f("ck_models_name_normalized"),
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL) = (purge_at IS NULL)", name=op.f("ck_models_soft_delete_pair")
        ),
        sa.CheckConstraint("purge_at > deleted_at", name=op.f("ck_models_purge_after_delete")),
        sa.CheckConstraint("row_version >= 1", name=op.f("ck_models_row_version_positive")),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_models_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_models_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_models")),
    )
    # 같은 조직 안에서 활성 모델 이름 중복 방지 (소프트 삭제된 모델은 제외)
    op.create_index(
        "uq_models_organization_id_name_active",
        "models",
        ["organization_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # 목록 조회 keyset 페이지네이션
    op.create_index(
        "ix_models_organization_id_created_at_id",
        "models",
        ["organization_id", "created_at", "id"],
    )
    # 영구 삭제 배치 대상 조회 (ADR 0005)
    op.create_index(
        "ix_models_purge_at",
        "models",
        ["purge_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )

    op.create_table(
        "model_versions",
        _uuid_pk(),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'PENDING'"), nullable=False
        ),
        sa.Column("artifact_uri", sa.Text(), nullable=True),
        sa.Column("framework", sa.String(length=50), nullable=True),
        sa.Column("input_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('PENDING', 'TRAINING', 'READY', 'FAILED', 'ARCHIVED')",
            name=op.f("ck_model_versions_status"),
        ),
        sa.CheckConstraint("version_no >= 1", name=op.f("ck_model_versions_version_no_positive")),
        sa.CheckConstraint(
            "parent_version_id <> id", name=op.f("ck_model_versions_parent_not_self")
        ),
        sa.CheckConstraint(
            "status NOT IN ('READY', 'ARCHIVED') OR artifact_uri IS NOT NULL",
            name=op.f("ck_model_versions_ready_has_artifact"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["models.id"],
            name=op.f("fk_model_versions_model_id_models"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_model_versions_created_by_users"),
            ondelete="RESTRICT",
        ),
        # 부모 버전은 같은 모델에 속해야 한다 (parent_version_id가 NULL이면 검사하지 않음)
        sa.ForeignKeyConstraint(
            ["model_id", "parent_version_id"],
            ["model_versions.model_id", "model_versions.id"],
            name="fk_model_versions_parent_version",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_versions")),
        sa.UniqueConstraint(
            "model_id", "version_no", name=op.f("uq_model_versions_model_id_version_no")
        ),
        sa.UniqueConstraint("model_id", "id", name=op.f("uq_model_versions_model_id_id")),
    )


def downgrade() -> None:
    op.drop_table("model_versions")
    op.drop_index("ix_models_purge_at", table_name="models")
    op.drop_index("ix_models_organization_id_created_at_id", table_name="models")
    op.drop_index("uq_models_organization_id_name_active", table_name="models")
    op.drop_table("models")
    op.drop_index(op.f("ix_users_organization_id"), table_name="users")
    op.drop_table("users")
    op.drop_table("organizations")
