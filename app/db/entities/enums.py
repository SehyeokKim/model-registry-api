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
