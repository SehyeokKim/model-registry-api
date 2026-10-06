import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, string_enum
from app.db.entities.enums import ModelVersionStatus

if TYPE_CHECKING:
    from app.db.entities.ml_model import MLModel


class MLModelVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """모델의 불변 버전. 학습 결과물 하나에 해당한다."""

    __tablename__ = "model_versions"
    __table_args__ = (
        # 버전 번호 중복 방지의 최종 방어선 (ADR 0003)
        UniqueConstraint("model_id", "version_no"),
        # 아래 복합 FK의 참조 대상
        UniqueConstraint("model_id", "id"),
        # 부모 버전은 반드시 같은 모델에 속해야 한다. parent_version_id가 NULL이면 검사하지 않는다.
        ForeignKeyConstraint(
            ["model_id", "parent_version_id"],
            ["model_versions.model_id", "model_versions.id"],
            name="fk_model_versions_parent_version",
        ),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint("parent_version_id <> id", name="parent_not_self"),
        CheckConstraint(
            "status NOT IN ('READY', 'ARCHIVED') OR artifact_uri IS NOT NULL",
            name="ready_has_artifact",
        ),
    )

    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"))
    version_no: Mapped[int]
    parent_version_id: Mapped[uuid.UUID | None]
    status: Mapped[ModelVersionStatus] = mapped_column(
        string_enum(ModelVersionStatus, "status"),
        server_default=text(f"'{ModelVersionStatus.PENDING.value}'"),
    )
    # 저장 경로는 version_no가 아니라 UUID로 만든다: models/{model_id}/versions/{version_id}/
    artifact_uri: Mapped[str | None] = mapped_column(Text)
    framework: Mapped[str | None] = mapped_column(String(50))
    # 스키마 문서는 클 수 있고 모델 조회 응답에는 쓰지 않으므로 기본으로 읽지 않는다.
    # 필요한 곳에서 undefer로 명시하고, 실수로 접근하면 추가 쿼리 대신 예외가 나게 한다.
    input_schema: Mapped[dict[str, Any] | None] = mapped_column(
        deferred=True, deferred_raiseload=True
    )
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(
        deferred=True, deferred_raiseload=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    model: Mapped["MLModel"] = relationship(back_populates="versions", lazy="raise")
