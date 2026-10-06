"""ORM 엔티티. 이 패키지를 import하면 모든 테이블이 Base.metadata에 등록된다 (Alembic이 사용)."""

from app.db.entities.enums import ModelStatusFilter, ModelVersionStatus, TaskType, UserRole
from app.db.entities.ml_model import MLModel
from app.db.entities.ml_model_version import MLModelVersion
from app.db.entities.organization import Organization
from app.db.entities.user import User

__all__ = [
    "MLModel",
    "MLModelVersion",
    "ModelStatusFilter",
    "ModelVersionStatus",
    "Organization",
    "TaskType",
    "User",
    "UserRole",
]
