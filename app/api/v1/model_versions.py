"""모델 버전 API."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import CustomerDep, ModelVersionServiceDep
from app.core.errors import ErrorCode
from app.schemas.error import error_responses
from app.schemas.model_version import ModelVersionCreateRequest, ModelVersionResponse

router = APIRouter(prefix="/models/{model_id}/versions", tags=["Model Versions"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ModelVersionResponse,
    summary="모델 버전 생성",
    description=(
        "모델에 새 버전을 `PENDING` 상태로 만든다. "
        "버전 번호는 모델별로 1부터 순차 증가하며, 동시에 생성해도 겹치지 않는다.\n\n"
        "- 이전 버전이 학습 중이어도 새 버전을 만들 수 있다.\n"
        "- `parent_version_id`는 같은 모델의 `READY` 버전만 가능하다.\n"
        "- 버전은 생성 후 바꿀 수 없으므로 `input_schema`, `output_schema`는 저장 시점에 "
        "JSON Schema Draft 2020-12 문서로 올바른지 검증한다. "
        "실패하면 `VALIDATION_ERROR`이고 `details`에 오류 위치를 담는다.\n"
        "- 같은 모델의 다른 쓰기와 충돌해 `MODEL_VERSION_CONFLICT`가 나면 다시 요청한다."
    ),
    responses=error_responses(
        ErrorCode.UNAUTHORIZED,
        ErrorCode.FORBIDDEN,
        ErrorCode.MODEL_NOT_FOUND,
        ErrorCode.MODEL_VERSION_CONFLICT,
        ErrorCode.INVALID_PARENT_VERSION,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.SERVICE_UNAVAILABLE,
    ),
)
def create_model_version(
    model_id: uuid.UUID,
    body: ModelVersionCreateRequest,
    requester: CustomerDep,
    service: ModelVersionServiceDep,
) -> ModelVersionResponse:
    version = service.create(
        requester,
        model_id,
        parent_version_id=body.parent_version_id,
        framework=body.framework,
        input_schema=body.input_schema,
        output_schema=body.output_schema,
    )
    return ModelVersionResponse.model_validate(version)
