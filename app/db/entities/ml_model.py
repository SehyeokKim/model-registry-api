import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, string_enum
from app.db.entities.enums import TaskType

if TYPE_CHECKING:
    from app.db.entities.ml_model_version import MLModelVersion


class MLModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """모델 이름표. 학습 결과물은 MLModelVersion으로 관리한다.

    클래스 이름은 ORM/Pydantic의 "model" 용어와 구분하기 위해 MLModel로 쓴다.
    """

    __tablename__ = "models"
    __table_args__ = (
        CheckConstraint("(deleted_at IS NULL) = (purge_at IS NULL)", name="soft_delete_pair"),
        CheckConstraint("purge_at > deleted_at", name="purge_after_delete"),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        # 활성 이름 중복 방지 인덱스가 정확히 동작하려면 이름이 정규화되어 있어야 한다.
        CheckConstraint(
            "name IS NFC NORMALIZED AND name = btrim(name) AND char_length(name) >= 1",
            name="name_normalized",
        ),
        Index(
            "uq_models_organization_id_name_active",
            "organization_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 목록 조회(keyset): ORDER BY created_at DESC, id DESC는 이 인덱스를 역방향으로 읽는다.
        Index("ix_models_organization_id_created_at_id", "organization_id", "created_at", "id"),
        # 영구 삭제 배치 대상 조회 (ADR 0005)
        Index("ix_models_purge_at", "purge_at", postgresql_where=text("deleted_at IS NOT NULL")),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(100))
    task_type: Mapped[TaskType] = mapped_column(string_enum(TaskType, "task_type"))
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None]
    purge_at: Mapped[datetime | None]
    # ETag 값. 단건 응답이 바뀌는 모든 쓰기에서 1씩 올린다 (ADR 0007).
    row_version: Mapped[int] = mapped_column(server_default=text("1"))

    # 지연 로딩을 금지한다. 필요한 곳에서 selectinload로 명시적으로 불러오고,
    # 실수로 N+1 쿼리가 생기면 즉시 예외가 나게 한다.
    versions: Mapped[list["MLModelVersion"]] = relationship(
        back_populates="model",
        order_by="desc(MLModelVersion.version_no)",
        lazy="raise",
        passive_deletes=True,
    )
