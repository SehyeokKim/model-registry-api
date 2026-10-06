"""모델 버전 API 요청/응답 DTO."""

import json
import uuid
from typing import Annotated, Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

from app.db.entities import ModelVersionStatus
from app.schemas.common import UtcDatetime

FRAMEWORK_MAX_LENGTH = 50
# 스키마 문서 하나의 최대 크기(JSON 직렬화 기준). 버전은 불변이라 저장된 문서가 끝까지 남고,
# 추론 게이트웨이와 클라이언트도 이 문서를 읽으므로 비정상적으로 큰 문서를 막는다.
JSON_SCHEMA_MAX_BYTES = 64 * 1024

_META_SCHEMA_VALIDATOR = Draft202012Validator(
    Draft202012Validator.META_SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER
)


def _check_json_schema(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """스키마 문서 자체가 JSON Schema Draft 2020-12로 올바른지 확인한다.

    실제 데이터셋 컬럼과 맞는지는 확인하지 않는다 (학습 파이프라인의 책임).
    """
    if value is None:
        return None
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
    if size > JSON_SCHEMA_MAX_BYTES:
        raise ValueError(f"JSON Schema must be at most {JSON_SCHEMA_MAX_BYTES} bytes")
    error = best_match(_META_SCHEMA_VALIDATOR.iter_errors(value))
    if error is not None:
        pointer = "".join(f"/{part}" for part in error.absolute_path) or "/"
        raise ValueError(f"Invalid JSON Schema at {pointer}: {error.message}")
    return value


def _strip(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


JsonSchemaDocument = Annotated[
    dict[str, Any] | None,
    AfterValidator(_check_json_schema),
]


class ModelVersionCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "framework": "xgboost",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "tenure_months": {"type": "integer", "minimum": 0},
                            "monthly_charge": {"type": "number"},
                        },
                        "required": ["tenure_months", "monthly_charge"],
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {"churn": {"type": "boolean"}},
                    },
                }
            ]
        },
    )

    parent_version_id: uuid.UUID | None = Field(
        default=None, description="재학습의 기준 버전. 같은 모델의 `READY` 버전만 가능"
    )
    framework: Annotated[
        str | None,
        BeforeValidator(_strip),
        Field(min_length=1, max_length=FRAMEWORK_MAX_LENGTH),
    ] = Field(default=None, description="학습 프레임워크 (예: xgboost, lightgbm). 1–50자")
    input_schema: JsonSchemaDocument = Field(
        default=None,
        description="추론 입력 형식. JSON Schema Draft 2020-12 문서, 직렬화 기준 64 KiB 이하",
    )
    output_schema: JsonSchemaDocument = Field(
        default=None,
        description="추론 출력 형식. JSON Schema Draft 2020-12 문서, 직렬화 기준 64 KiB 이하",
    )


class ModelVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    model_id: uuid.UUID
    version_no: int = Field(description="모델별 1부터 순차 증가하는 표시용 번호")
    parent_version_id: uuid.UUID | None = Field(description="재학습의 기준이 된 버전")
    status: ModelVersionStatus = Field(description="생성 직후에는 `PENDING`")
    framework: str | None
    artifact_uri: str | None = Field(description="학습 완료(READY) 후 기록되는 아티팩트 위치")
    input_schema: dict[str, Any] | None
    output_schema: dict[str, Any] | None
    created_by: uuid.UUID = Field(description="버전을 만든 사용자")
    created_at: UtcDatetime
