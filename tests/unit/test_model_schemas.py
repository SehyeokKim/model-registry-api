import unicodedata

import pytest
from pydantic import ValidationError

from app.db.entities import TaskType
from app.schemas.model import ModelCreateRequest, ModelUpdateRequest


class TestModelName:
    def test_name_is_nfc_normalized_and_trimmed(self) -> None:
        decomposed = unicodedata.normalize("NFD", "  한국모델  ")

        request = ModelCreateRequest(name=decomposed, task_type=TaskType.CLASSIFICATION)

        assert request.name == unicodedata.normalize("NFC", "한국모델")

    def test_full_width_characters_are_kept(self) -> None:
        """NFC는 전각/반각을 바꾸지 않는다 (NFKC를 쓰지 않는 이유)."""
        full_width = "ＡＢＣ-ｶ"  # noqa: RUF001  전각 ABC, 반각 가타카나

        request = ModelCreateRequest(name=full_width, task_type=TaskType.REGRESSION)

        assert request.name == full_width

    @pytest.mark.parametrize("name", ["", "   ", "\t\n"])
    def test_blank_name_is_rejected(self, name: str) -> None:
        with pytest.raises(ValidationError):
            ModelCreateRequest(name=name, task_type=TaskType.CLASSIFICATION)

    def test_name_length_is_counted_after_trimming(self) -> None:
        ModelCreateRequest(name=" " + "a" * 100 + " ", task_type=TaskType.CLASSIFICATION)

        with pytest.raises(ValidationError):
            ModelCreateRequest(name="a" * 101, task_type=TaskType.CLASSIFICATION)


class TestCreateRequest:
    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelCreateRequest.model_validate(
                {"name": "m", "task_type": "CLASSIFICATION", "owner": "x"}
            )

    def test_unknown_task_type_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelCreateRequest.model_validate({"name": "m", "task_type": "CLUSTERING"})

    def test_description_is_limited_to_2000_characters(self) -> None:
        ModelCreateRequest(name="m", task_type=TaskType.CLASSIFICATION, description="가" * 2000)

        with pytest.raises(ValidationError):
            ModelCreateRequest(name="m", task_type=TaskType.CLASSIFICATION, description="가" * 2001)


class TestUpdateRequest:
    def test_only_sent_fields_are_reported(self) -> None:
        request = ModelUpdateRequest.model_validate({"description": None})

        assert request.model_dump(exclude_unset=True) == {"description": None}

    def test_empty_request_has_no_changes(self) -> None:
        assert ModelUpdateRequest.model_validate({}).model_dump(exclude_unset=True) == {}

    @pytest.mark.parametrize("field", ["name", "task_type"])
    def test_required_fields_cannot_be_null(self, field: str) -> None:
        with pytest.raises(ValidationError):
            ModelUpdateRequest.model_validate({field: None})

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelUpdateRequest.model_validate({"organization_id": "x"})


class TestUnstorableText:
    """PostgreSQL에 저장할 수 없는 문자는 저장 단계의 500이 아니라 입력 검증에서 거부한다."""

    @pytest.mark.parametrize("text", ["a\x00b", "a\ud800"])
    def test_name_with_unstorable_character_is_rejected(self, text: str) -> None:
        with pytest.raises(ValidationError):
            ModelCreateRequest(name=text, task_type=TaskType.CLASSIFICATION)

    @pytest.mark.parametrize("text", ["a\x00b", "a\udfff"])
    def test_description_with_unstorable_character_is_rejected(self, text: str) -> None:
        with pytest.raises(ValidationError):
            ModelUpdateRequest(description=text)
