"""모델 API 요청/응답 DTO."""

import unicodedata
import uuid
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

from app.db.entities import ModelVersionStatus, TaskType
from app.schemas.common import UtcDatetime, check_storable_text

NAME_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 2000


def _normalize_name(value: Any) -> Any:
    """NFC 정규화 후 앞뒤 공백을 제거한다. 문자열이 아니면 타입 검증에 맡긴다.

    같은 글자가 조합형/분해형으로 달리 들어와도 같은 이름으로 비교되게 한다.
    """
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


ModelName = Annotated[
    str,
    BeforeValidator(_normalize_name),
    Field(
        min_length=1,
        max_length=NAME_MAX_LENGTH,
        description=(
            "모델 이름. NFC 정규화와 앞뒤 공백 제거 후 1–100자. 같은 조직의 활성 모델과 중복 불가"
        ),
        examples=["churn-predictor"],
    ),
    AfterValidator(check_storable_text),
]
Description = Annotated[
    str | None,
    Field(max_length=DESCRIPTION_MAX_LENGTH, description="설명 (최대 2000자)"),
    AfterValidator(check_storable_text),
]


class ModelCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "churn-predictor",
                    "task_type": "CLASSIFICATION",
                    "description": "고객 이탈 예측",
                }
            ]
        },
    )

    name: ModelName
    task_type: TaskType = Field(description="학습 유형")
    description: Description = None


class ModelUpdateRequest(BaseModel):
    """JSON Merge Patch(RFC 7396) 의미의 수정 요청.

    생략한 필드는 바꾸지 않고, `description: null`은 설명을 비운다.
    `name`, `task_type`은 필수 항목이라 null을 허용하지 않는다.
    기본값 None은 "보내지 않음"을 뜻하며, 실제로 보낸 필드는 `model_fields_set`으로 구분한다.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"name": "churn-predictor-v2", "description": None}]},
    )

    name: ModelName = None  # type: ignore[assignment]
    task_type: TaskType = Field(
        default=None,  # type: ignore[assignment]
        description="버전이 하나도 없을 때만 바꿀 수 있다",
    )
    description: Description = None


class ModelVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version_no: int = Field(description="모델별 1부터 순차 증가하는 표시용 번호")
    parent_version_id: uuid.UUID | None = Field(description="재학습의 기준이 된 버전")
    status: ModelVersionStatus
    framework: str | None
    artifact_uri: str | None = Field(description="학습 완료(READY) 후 기록되는 아티팩트 위치")
    created_at: UtcDatetime


class ModelDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID = Field(description="모델을 소유한 조직")
    created_by: uuid.UUID = Field(description="모델을 만든 사용자")
    name: str
    task_type: TaskType
    description: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    versions: list[ModelVersionSummary] = Field(description="버전 목록 (최신 버전부터)")


class ModelDeletedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    deleted_at: UtcDatetime
    purge_at: UtcDatetime = Field(description="이 시각까지 복원할 수 있다. 이후 영구 삭제된다.")


class ModelListItem(BaseModel):
    """목록 항목. 버전 목록 대신 버전 집계만 담는다."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID = Field(description="모델을 소유한 조직")
    created_by: uuid.UUID = Field(description="모델을 만든 사용자")
    name: str
    task_type: TaskType
    description: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    deleted_at: UtcDatetime | None = Field(description="삭제 시각. 활성 모델은 null")
    purge_at: UtcDatetime | None = Field(
        description="이 시각까지 복원할 수 있다. 이후 영구 삭제된다. 활성 모델은 null"
    )
    version_count: int = Field(description="버전 수 (모든 상태 포함)")
    latest_version_no: int | None = Field(description="가장 최근 버전 번호. 버전이 없으면 null")


class ModelListResponse(BaseModel):
    items: list[ModelListItem]
    next_cursor: str | None = Field(
        description="다음 페이지 커서. 그대로 `cursor`에 넣어 요청한다. 마지막 페이지면 null"
    )
