"""모델 API (Core CRUD)."""

import uuid

from fastapi import APIRouter, Request, Response, status

from app.api.deps import CustomerDep, ModelServiceDep
from app.core.errors import ErrorCode
from app.schemas.error import error_responses
from app.schemas.model import (
    ModelCreateRequest,
    ModelDeletedResponse,
    ModelDetailResponse,
    ModelUpdateRequest,
)

router = APIRouter(prefix="/models", tags=["Models"])

# 모든 고객 API가 낼 수 있는 공통 에러
_COMMON_ERRORS = (
    ErrorCode.UNAUTHORIZED,
    ErrorCode.FORBIDDEN,
    ErrorCode.VALIDATION_ERROR,
    ErrorCode.SERVICE_UNAVAILABLE,
)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ModelDetailResponse,
    summary="모델 생성",
    description=(
        "요청자 조직에 새 모델을 만든다. 버전은 비어 있는 상태로 시작한다.\n\n"
        "- 이름은 NFC 정규화와 앞뒤 공백 제거 후 저장한다.\n"
        "- 같은 조직의 활성 모델 이름과 겹칠 수 없다.\n"
        "- 응답의 `Location` 헤더에 생성된 모델의 경로를 담는다."
    ),
    responses=error_responses(*_COMMON_ERRORS, ErrorCode.MODEL_NAME_CONFLICT),
)
def create_model(
    body: ModelCreateRequest,
    requester: CustomerDep,
    service: ModelServiceDep,
    request: Request,
    response: Response,
) -> ModelDetailResponse:
    model = service.create(
        requester, name=body.name, task_type=body.task_type, description=body.description
    )
    response.headers["Location"] = request.url_for("get_model", model_id=model.id).path
    return ModelDetailResponse.model_validate(model)


@router.get(
    "/{model_id}",
    response_model=ModelDetailResponse,
    summary="모델 단건 조회",
    description=(
        "모델 정보와 버전 목록(최신 버전부터)을 조회한다. "
        "삭제된 모델과 다른 조직의 모델은 존재 여부를 드러내지 않도록 404로 응답한다."
    ),
    responses=error_responses(*_COMMON_ERRORS, ErrorCode.MODEL_NOT_FOUND),
)
def get_model(
    model_id: uuid.UUID, requester: CustomerDep, service: ModelServiceDep
) -> ModelDetailResponse:
    return ModelDetailResponse.model_validate(service.get(requester, model_id))


@router.patch(
    "/{model_id}",
    response_model=ModelDetailResponse,
    summary="모델 수정",
    description=(
        "JSON Merge Patch(RFC 7396) 의미로 수정한다.\n\n"
        "- 보내지 않은 필드는 바꾸지 않는다. `description: null`은 설명을 비운다.\n"
        "- `name`, `task_type`은 null을 보낼 수 없다. 빈 요청 `{}`은 `EMPTY_UPDATE`다.\n"
        "- `task_type`은 버전이 하나도 없을 때만 바꿀 수 있다. 버전이 생긴 뒤 유형이 바뀌면 "
        "추론 결과 형식, 성능 지표, 재학습 계보가 모델 정의와 어긋나기 때문이다."
    ),
    responses=error_responses(
        *_COMMON_ERRORS,
        ErrorCode.MODEL_NOT_FOUND,
        ErrorCode.MODEL_NAME_CONFLICT,
        ErrorCode.TASK_TYPE_LOCKED,
        ErrorCode.EMPTY_UPDATE,
    ),
)
def update_model(
    model_id: uuid.UUID,
    body: ModelUpdateRequest,
    requester: CustomerDep,
    service: ModelServiceDep,
) -> ModelDetailResponse:
    model = service.update(requester, model_id, body.model_dump(exclude_unset=True))
    return ModelDetailResponse.model_validate(model)


@router.delete(
    "/{model_id}",
    response_model=ModelDeletedResponse,
    summary="모델 삭제 (소프트 삭제)",
    description=(
        "모델을 삭제 상태로 바꾼다. 응답의 `purge_at`까지 복원할 수 있고, 이후 영구 삭제된다.\n\n"
        "삭제된 모델은 조회, 수정, 삭제 대상에서 제외된다(404). "
        "같은 이름으로 새 모델을 만들 수 있다."
    ),
    responses=error_responses(*_COMMON_ERRORS, ErrorCode.MODEL_NOT_FOUND),
)
def delete_model(
    model_id: uuid.UUID, requester: CustomerDep, service: ModelServiceDep
) -> ModelDeletedResponse:
    return ModelDeletedResponse.model_validate(service.delete(requester, model_id))
