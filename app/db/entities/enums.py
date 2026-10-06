"""도메인 열거형. DB 컬럼과 API 스키마가 함께 사용한다."""

from enum import StrEnum


class UserRole(StrEnum):
    CUSTOMER = "CUSTOMER"
    ML_ENGINEER = "ML_ENGINEER"
    ADMIN = "ADMIN"


class TaskType(StrEnum):
    CLASSIFICATION = "CLASSIFICATION"
    REGRESSION = "REGRESSION"


class ModelVersionStatus(StrEnum):
    PENDING = "PENDING"
    TRAINING = "TRAINING"
    READY = "READY"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class ModelStatusFilter(StrEnum):
    """모델 목록 조회 범위. DB 컬럼이 아니라 조회 조건이다.

    복원 기한이 지난 모델은 영구 삭제 대기 중일 뿐 이미 삭제가 확정되었으므로
    어느 범위에도 넣지 않는다.
    """

    ACTIVE = "active"
    DELETED = "deleted"
    ALL = "all"
