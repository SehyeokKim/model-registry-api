from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.model_version import (
    JSON_SCHEMA_MAX_BYTES,
    JSON_SCHEMA_MAX_DEPTH,
    ModelVersionCreateRequest,
)


def _error_messages(error: ValidationError) -> list[str]:
    return [e["msg"] for e in error.errors()]


class TestJsonSchemaValidation:
    @pytest.mark.parametrize(
        "schema",
        [
            {},
            {"type": "number"},
            {
                "type": "object",
                "properties": {
                    "age": {"type": "integer", "minimum": 0},
                    "name": {"type": "string"},
                },
                "required": ["age"],
                "additionalProperties": False,
            },
            {"$defs": {"id": {"type": "string"}}, "items": {"$ref": "#/$defs/id"}},
        ],
    )
    def test_valid_draft_2020_12_schema_is_accepted(self, schema: dict[str, Any]) -> None:
        request = ModelVersionCreateRequest(input_schema=schema, output_schema=schema)

        assert request.input_schema == schema

    @pytest.mark.parametrize(
        ("schema", "pointer"),
        [
            ({"type": "strin"}, "/type"),
            ({"required": "age"}, "/required"),
            ({"properties": {"age": {"type": "integr"}}}, "/properties/age/type"),
            ({"minimum": "zero"}, "/minimum"),
        ],
    )
    def test_invalid_schema_reports_location(self, schema: dict[str, Any], pointer: str) -> None:
        with pytest.raises(ValidationError) as caught:
            ModelVersionCreateRequest(input_schema=schema)

        (error,) = caught.value.errors()
        assert error["loc"] == ("input_schema",)
        assert f"Invalid JSON Schema at {pointer}:" in error["msg"]

    def test_schema_must_be_an_object(self) -> None:
        with pytest.raises(ValidationError):
            ModelVersionCreateRequest(output_schema=["type", "number"])  # type: ignore[arg-type]

    def test_schema_size_is_limited(self) -> None:
        # 직렬화 크기가 상한을 넘도록 description을 채운다.
        oversized = {"type": "object", "description": "x" * JSON_SCHEMA_MAX_BYTES}

        with pytest.raises(ValidationError) as caught:
            ModelVersionCreateRequest(input_schema=oversized)

        assert "at most 65536 bytes" in _error_messages(caught.value)[0]

    def test_size_is_counted_in_utf8_bytes(self) -> None:
        # 한글은 UTF-8로 3바이트이므로 글자 수가 상한의 1/3을 넘으면 거절된다.
        korean = {"description": "가" * (JSON_SCHEMA_MAX_BYTES // 3 + 1)}

        with pytest.raises(ValidationError):
            ModelVersionCreateRequest(input_schema=korean)


class TestFramework:
    def test_surrounding_whitespace_is_removed(self) -> None:
        assert ModelVersionCreateRequest(framework="  xgboost ").framework == "xgboost"

    @pytest.mark.parametrize("framework", ["", "   ", "x" * 51])
    def test_blank_or_too_long_is_rejected(self, framework: str) -> None:
        with pytest.raises(ValidationError):
            ModelVersionCreateRequest(framework=framework)


def test_empty_body_creates_version_without_details() -> None:
    request = ModelVersionCreateRequest()

    assert request.model_dump() == {
        "parent_version_id": None,
        "framework": None,
        "input_schema": None,
        "output_schema": None,
    }


def test_undefined_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelVersionCreateRequest(status="READY")  # type: ignore[call-arg]


def _nested_list(depth: int) -> Any:
    value: Any = 0
    for _ in range(depth):
        value = [value]
    return value


class TestJsonSchemaStructure:
    def test_schema_at_max_depth_is_accepted(self) -> None:
        # 객체(깊이 1) 안의 const 배열 31단계 = 깊이 32
        schema = {"const": _nested_list(JSON_SCHEMA_MAX_DEPTH - 1)}

        request = ModelVersionCreateRequest(input_schema=schema)

        assert request.input_schema == schema

    def test_deeply_nested_value_is_rejected(self) -> None:
        """작은 문서라도 깊으면 응답 직렬화의 재귀 한도를 넘겨 저장 후 500이 된다."""
        schema = {"const": _nested_list(120)}

        with pytest.raises(ValidationError) as error:
            ModelVersionCreateRequest(input_schema=schema)

        assert "nested at most" in _error_messages(error.value)[0]

    def test_deeply_nested_keywords_are_rejected_without_recursion_error(self) -> None:
        schema: dict[str, Any] = {}
        for _ in range(300):
            schema = {"items": schema}

        with pytest.raises(ValidationError):
            ModelVersionCreateRequest(output_schema=schema)

    @pytest.mark.parametrize(
        "schema",
        [
            {"description": "a\x00"},
            {"properties": {"a\x00": {"type": "string"}}},
            {"enum": ["\ud800"]},
        ],
    )
    def test_unstorable_text_in_schema_is_rejected(self, schema: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ModelVersionCreateRequest(input_schema=schema)


def test_framework_with_nul_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelVersionCreateRequest(framework="xgb\x00")
