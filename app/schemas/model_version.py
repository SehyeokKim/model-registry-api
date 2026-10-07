"""모델 버전 API 요청/응답 DTO."""

import json
import uuid
from typing import Annotated, Any, Self
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from app.db.entities import ModelVersionStatus
from app.schemas.common import UtcDatetime, check_storable_text

FRAMEWORK_MAX_LENGTH = 50
ARTIFACT_URI_MAX_LENGTH = 2048
# 스키마 문서 하나의 최대 크기(JSON 직렬화 기준). 버전은 불변이라 저장된 문서가 끝까지 남고,
# 추론 게이트웨이와 클라이언트도 이 문서를 읽으므로 비정상적으로 큰 문서를 막는다.
JSON_SCHEMA_MAX_BYTES = 64 * 1024
# 객체와 배열의 최대 중첩 깊이. 실제 입출력 스키마는 깊이 10을 넘는 일이 드물다.
# 깊은 문서는 크기가 작아도 검증기와 응답 직렬화의 재귀 한도를 넘겨, 저장 후 500이 된다.
JSON_SCHEMA_MAX_DEPTH = 32

# format 검사의 `regex`는 Python `re`로 컴파일한다. 추론 게이트웨이도 Python으로 요청을 검증하므로,
# 여기서 컴파일되지 않는 패턴(`\p{L}` 등)은 추론 시점에 실패한다. 저장 시점에 거부한다.
_META_SCHEMA_VALIDATOR = Draft202012Validator(
    Draft202012Validator.META_SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER
)


def _check_structure(value: dict[str, Any]) -> None:
    """중첩 깊이와 저장할 수 없는 문자를 재귀 없이 확인한다."""
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict | list):
            if depth > JSON_SCHEMA_MAX_DEPTH:
                raise ValueError(
                    f"JSON Schema must be nested at most {JSON_SCHEMA_MAX_DEPTH} levels"
                )
            children = node.items() if isinstance(node, dict) else enumerate(node)
            for key, child in children:
                check_storable_text(key)
                stack.append((child, depth + 1))
        else:
            check_storable_text(node)


def _check_json_schema(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """스키마 문서 자체가 JSON Schema Draft 2020-12로 올바른지 확인한다.

    실제 데이터셋 컬럼과 맞는지는 확인하지 않는다 (학습 파이프라인의 책임).
    """
    if value is None:
        return None
    _check_structure(value)
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


def _check_absolute_uri(value: str | None) -> str | None:
    if value is None:
        return None
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc or any(c.isspace() for c in value):
        raise ValueError("artifact_uri must be an absolute URI such as s3://bucket/path/")
    return value


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
        AfterValidator(check_storable_text),
    ] = Field(default=None, description="학습 프레임워크 (예: xgboost, lightgbm). 1–50자")
    input_schema: JsonSchemaDocument = Field(
        default=None,
        description=(
            "추론 입력 형식. JSON Schema Draft 2020-12 문서, 직렬화 기준 64 KiB 이하, "
            "중첩 깊이 32 이하. `pattern`은 Python 정규식으로 컴파일할 수 있어야 한다"
        ),
    )
    output_schema: JsonSchemaDocument = Field(
        default=None,
        description=(
            "추론 출력 형식. JSON Schema Draft 2020-12 문서, 직렬화 기준 64 KiB 이하, "
            "중첩 깊이 32 이하. `pattern`은 Python 정규식으로 컴파일할 수 있어야 한다"
        ),
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


class ModelVersionStatusUpdateRequest(BaseModel):
    """버전 상태 변경 요청. `READY`로 바꿀 때만 `artifact_uri`를 받는다."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"status": "TRAINING"},
                {
                    "status": "READY",
                    "artifact_uri": (
                        "s3://model-artifacts/models/<model_id>/versions/<version_id>/model.tar.gz"
                    ),
                },
            ]
        },
    )

    status: ModelVersionStatus = Field(description="바꿀 상태")
    artifact_uri: Annotated[
        str | None,
        BeforeValidator(_strip),
        Field(min_length=1, max_length=ARTIFACT_URI_MAX_LENGTH),
        AfterValidator(check_storable_text),
        AfterValidator(_check_absolute_uri),
    ] = Field(
        default=None,
        description=(
            "학습 결과 아티팩트 위치. `READY`로 바꿀 때 필수이고 다른 상태에서는 보낼 수 없다. "
            "스킴이 있는 절대 URI이며, 경로의 첫 `models` 세그먼트 바로 뒤가 "
            "`{model_id}/versions/{version_id}/`여야 한다. `.`, `..` 세그먼트는 허용하지 않는다."
        ),
    )

    @model_validator(mode="after")
    def _artifact_uri_only_for_ready(self) -> Self:
        if self.status == ModelVersionStatus.READY and self.artifact_uri is None:
            raise ValueError("artifact_uri is required when status is READY")
        if self.status != ModelVersionStatus.READY and self.artifact_uri is not None:
            raise ValueError("artifact_uri can only be set when status is READY")
        return self
