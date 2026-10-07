"""모델 API (생성, 조회, 수정, 삭제, 목록, 복원)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response, status

from app.api.deps import CustomerDep, ModelServiceDep
from app.core.errors import ErrorCode
from app.core.etag import IF_MATCH_PATTERN, IfMatch, format_etag, parse_if_match
from app.core.exceptions import AppError
from app.db.entities import MLModel, ModelStatusFilter
from app.schemas.error import error_responses
from app.schemas.model import (
    ModelCreateRequest,
    ModelDeletedResponse,
    ModelDetailResponse,
    ModelListItem,
    ModelListResponse,
    ModelUpdateRequest,
)
from app.schemas.pagination import InvalidCursorError, decode_cursor, encode_cursor

router = APIRouter(prefix="/models", tags=["Models"])

# 모든 고객 API가 낼 수 있는 공통 에러
_COMMON_ERRORS = (
    ErrorCode.UNAUTHORIZED,
    ErrorCode.FORBIDDEN,
    ErrorCode.VALIDATION_ERROR,
    ErrorCode.SERVICE_UNAVAILABLE,
)

# 요청 본문을 받는 API만 낼 수 있는 에러 (UTF-8이 아닌 본문)
_BODY_ERRORS = (ErrorCode.BAD_REQUEST,)

# 모델 단건 응답의 ETag (ADR 0007)
_ETAG_HEADER = {
    "ETag": {
        "description": "모델의 현재 버전. 수정, 삭제, 복원 시 `If-Match`에 그대로 보낸다.",
        "schema": {"type": "string", "example": '"3"'},
    }
}

IfMatchHeader = Annotated[
    str | None,
    Header(
        pattern=IF_MATCH_PATTERN,
        description=(
            '조회 응답의 `ETag` 값(예: `"3"`). 보내면 현재 모델과 다를 때 '
            "`PRECONDITION_FAILED`로 거부해 다른 사람의 수정을 덮어쓰지 않는다. "
            "생략하면 비교 없이 처리한다. `*`는 모델이 있으면 통과한다."
        ),
    ),
]


def _with_etag(response: Response, model: MLModel) -> ModelDetailResponse:
    response.headers["ETag"] = format_etag(model.row_version)
    return ModelDetailResponse.model_validate(model)


def _if_match(value: str | None) -> IfMatch | None:
    return parse_if_match(value) if value is not None else None


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
    responses={
        201: {"headers": _ETAG_HEADER},
        **error_responses(*_BODY_ERRORS, *_COMMON_ERRORS, ErrorCode.MODEL_NAME_CONFLICT),
    },
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
    return _with_etag(response, model)


@router.get(
    "",
    response_model=ModelListResponse,
    summary="모델 목록 조회",
    description=(
        "요청자 조직의 모델을 최근 생성 순으로 조회한다. "
        "각 항목에는 버전 수와 최신 버전 번호를 담는다.\n\n"
        "- `status`: `active`(기본값), `deleted`(복원할 수 있는 삭제 모델, 휴지통 화면용), "
        "`all`(둘 다)\n"
        "- 복원 기한(`purge_at`)이 지난 모델은 삭제가 확정된 것으로 보고 "
        "어느 범위에도 넣지 않는다.\n"
        "- 커서 페이지네이션: 응답의 `next_cursor`를 다음 요청의 `cursor`에 그대로 넣는다. "
        "`next_cursor`가 null이면 마지막 페이지다. 커서는 같은 `status`로만 이어서 쓴다."
    ),
    responses=error_responses(*_COMMON_ERRORS),
)
def list_models(
    requester: CustomerDep,
    service: ModelServiceDep,
    status_filter: Annotated[
        ModelStatusFilter, Query(alias="status", description="조회 범위")
    ] = ModelStatusFilter.ACTIVE,
    limit: Annotated[int, Query(ge=1, le=100, description="페이지 크기")] = 20,
    cursor: Annotated[
        str | None, Query(description="이전 응답의 `next_cursor`. 첫 페이지는 생략")
    ] = None,
) -> ModelListResponse:
    after = None
    if cursor is not None:
        try:
            after = decode_cursor(cursor)
        except InvalidCursorError:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                details=[
                    {
                        "location": ["query", "cursor"],
                        "message": "Invalid cursor",
                        "type": "value_error",
                    }
                ],
            ) from None

    page = service.list_page(requester, status=status_filter, limit=limit, after=after)
    return ModelListResponse(
        items=[ModelListItem.model_validate(row) for row in page.items],
        next_cursor=encode_cursor(*page.next_position) if page.next_position else None,
    )


@router.get(
    "/{model_id}",
    response_model=ModelDetailResponse,
    summary="모델 단건 조회",
    description=(
        "모델 정보와 버전 목록(최신 버전부터)을 조회한다. "
        "삭제된 모델과 다른 조직의 모델은 존재 여부를 드러내지 않도록 404로 응답한다."
    ),
    responses={
        200: {"headers": _ETAG_HEADER},
        **error_responses(*_COMMON_ERRORS, ErrorCode.MODEL_NOT_FOUND),
    },
)
def get_model(
    model_id: uuid.UUID, requester: CustomerDep, service: ModelServiceDep, response: Response
) -> ModelDetailResponse:
    return _with_etag(response, service.get(requester, model_id))


@router.patch(
    "/{model_id}",
    response_model=ModelDetailResponse,
    summary="모델 수정",
    description=(
        "JSON Merge Patch(RFC 7396) 의미로 수정한다.\n\n"
        "- 보내지 않은 필드는 바꾸지 않는다. `description: null`은 설명을 비운다.\n"
        "- `name`, `task_type`은 null을 보낼 수 없다. 빈 요청 `{}`은 `EMPTY_UPDATE`다.\n"
        "- `task_type`은 버전이 하나도 없을 때만 바꿀 수 있다. 버전이 생긴 뒤 유형이 바뀌면 "
        "추론 결과 형식, 성능 지표, 재학습 계보가 모델 정의와 어긋나기 때문이다.\n"
        "- `If-Match`를 보내면 다른 사람이 먼저 수정했을 때 `PRECONDITION_FAILED`로 거부한다."
    ),
    responses={
        200: {"headers": _ETAG_HEADER},
        **error_responses(
            *_BODY_ERRORS,
            *_COMMON_ERRORS,
            ErrorCode.MODEL_NOT_FOUND,
            ErrorCode.MODEL_NAME_CONFLICT,
            ErrorCode.TASK_TYPE_LOCKED,
            ErrorCode.PRECONDITION_FAILED,
            ErrorCode.EMPTY_UPDATE,
        ),
    },
)
def update_model(
    model_id: uuid.UUID,
    body: ModelUpdateRequest,
    requester: CustomerDep,
    service: ModelServiceDep,
    response: Response,
    if_match: IfMatchHeader = None,
) -> ModelDetailResponse:
    model = service.update(
        requester, model_id, body.model_dump(exclude_unset=True), if_match=_if_match(if_match)
    )
    return _with_etag(response, model)


@router.delete(
    "/{model_id}",
    response_model=ModelDeletedResponse,
    summary="모델 삭제 (소프트 삭제)",
    description=(
        "모델을 삭제 상태로 바꾼다. 응답의 `purge_at`까지 복원할 수 있고, 이후 영구 삭제된다.\n\n"
        "삭제된 모델은 조회, 수정, 삭제 대상에서 제외된다(404). "
        "같은 이름으로 새 모델을 만들 수 있다. "
        "`If-Match`를 보내면 다른 사람이 먼저 수정했을 때 `PRECONDITION_FAILED`로 거부한다.\n\n"
        "삭제된 모델은 조회할 수 없으므로, 응답의 `ETag`를 복원 요청의 `If-Match`에 쓴다."
    ),
    responses={
        200: {"headers": _ETAG_HEADER},
        **error_responses(
            *_COMMON_ERRORS, ErrorCode.MODEL_NOT_FOUND, ErrorCode.PRECONDITION_FAILED
        ),
    },
)
def delete_model(
    model_id: uuid.UUID,
    requester: CustomerDep,
    service: ModelServiceDep,
    response: Response,
    if_match: IfMatchHeader = None,
) -> ModelDeletedResponse:
    model = service.delete(requester, model_id, if_match=_if_match(if_match))
    response.headers["ETag"] = format_etag(model.row_version)
    return ModelDeletedResponse.model_validate(model)


@router.post(
    "/{model_id}/restore",
    response_model=ModelDetailResponse,
    summary="삭제된 모델 복원",
    description=(
        "복원 기한(`purge_at`) 전의 삭제된 모델을 되살린다. "
        "버전 목록도 삭제 전 그대로 돌아온다.\n\n"
        "- 복원 기한이 지난 모델은 영구 삭제된 것으로 보고 복원할 수 없다(`MODEL_PURGED`). "
        "영구 삭제된 뒤에는 `MODEL_NOT_FOUND`다.\n"
        "- 그사이 같은 이름의 활성 모델이 생겼으면 `MODEL_NAME_CONFLICT`다. "
        "기존 모델의 이름을 바꾼 뒤 다시 요청한다.\n"
        "- `If-Match`를 보내면 다른 사람이 먼저 복원하거나 바꿨을 때 "
        "`PRECONDITION_FAILED`로 거부한다."
    ),
    responses={
        200: {"headers": _ETAG_HEADER},
        **error_responses(
            *_COMMON_ERRORS,
            ErrorCode.MODEL_NOT_FOUND,
            ErrorCode.MODEL_NOT_DELETED,
            ErrorCode.MODEL_NAME_CONFLICT,
            ErrorCode.MODEL_PURGED,
            ErrorCode.PRECONDITION_FAILED,
        ),
    },
)
def restore_model(
    model_id: uuid.UUID,
    requester: CustomerDep,
    service: ModelServiceDep,
    response: Response,
    if_match: IfMatchHeader = None,
) -> ModelDetailResponse:
    model = service.restore(requester, model_id, if_match=_if_match(if_match))
    return _with_etag(response, model)
