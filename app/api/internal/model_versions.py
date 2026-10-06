"""모델 버전 내부 API (학습 파이프라인용)."""

import uuid

from fastapi import APIRouter

from app.api.deps import InternalUserDep, ModelVersionServiceDep
from app.core.errors import ErrorCode
from app.schemas.error import error_responses
from app.schemas.model_version import ModelVersionResponse, ModelVersionStatusUpdateRequest

router = APIRouter(prefix="/model-versions", tags=["Internal"])


@router.patch(
    "/{version_id}/status",
    response_model=ModelVersionResponse,
    summary="모델 버전 상태 변경",
    description=(
        "학습 파이프라인이 학습 시작, 완료, 실패, 보관을 기록한다. "
        "내부 사용자만 호출할 수 있고, 고객이 학습 결과를 임의로 `READY`로 바꾸지 못하게 한다.\n\n"
        "| 현재 | 다음 | 의미 |\n"
        "|---|---|---|\n"
        "| `PENDING` | `TRAINING` | 학습 시작 |\n"
        "| `PENDING` | `FAILED` | 자원 할당 실패 등으로 시작 전 실패 |\n"
        "| `TRAINING` | `READY` | 학습 완료. `artifact_uri` 필수 |\n"
        "| `TRAINING` | `FAILED` | 학습 실패 |\n"
        "| `READY` | `ARCHIVED` | 더 이상 쓰지 않는 버전 보관 |\n\n"
        "- 그 외 전이(같은 상태로의 전이 포함)는 `INVALID_STATUS_TRANSITION`이다. "
        "`FAILED`, `ARCHIVED`는 최종 상태이며, 다시 학습하려면 새 버전을 만든다.\n"
        "- `artifact_uri`의 경로에는 `models/{model_id}/versions/{version_id}/`가 있어야 한다.\n"
        "- 소프트 삭제된 모델의 버전도 바꿀 수 있다. 복원 기한이 지난 모델은 영구 삭제된 것으로 "
        "보고 `MODEL_VERSION_NOT_FOUND`다. 파이프라인은 이 응답을 받으면 결과를 버리고 "
        "올린 아티팩트를 정리한다."
    ),
    responses=error_responses(
        ErrorCode.UNAUTHORIZED,
        ErrorCode.FORBIDDEN,
        ErrorCode.MODEL_VERSION_NOT_FOUND,
        ErrorCode.INVALID_STATUS_TRANSITION,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.SERVICE_UNAVAILABLE,
    ),
)
def change_model_version_status(
    version_id: uuid.UUID,
    body: ModelVersionStatusUpdateRequest,
    _user: InternalUserDep,
    service: ModelVersionServiceDep,
) -> ModelVersionResponse:
    version = service.change_status(version_id, status=body.status, artifact_uri=body.artifact_uri)
    return ModelVersionResponse.model_validate(version)
