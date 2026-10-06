import uuid
from itertools import product

import pytest
from pydantic import ValidationError

from app.core.exceptions import AppError
from app.db.entities import ModelVersionStatus as S
from app.schemas.model_version import ModelVersionStatusUpdateRequest
from app.services.model_version_service import ALLOWED_TRANSITIONS, _check_artifact_path

ALLOWED = {
    (S.PENDING, S.TRAINING),
    (S.PENDING, S.FAILED),
    (S.TRAINING, S.READY),
    (S.TRAINING, S.FAILED),
    (S.READY, S.ARCHIVED),
}


@pytest.mark.parametrize(("current", "target"), list(product(S, S)))
def test_only_documented_transitions_are_allowed(current: S, target: S) -> None:
    assert (target in ALLOWED_TRANSITIONS[current]) == ((current, target) in ALLOWED)


def test_failed_and_archived_are_final() -> None:
    assert ALLOWED_TRANSITIONS[S.FAILED] == frozenset()
    assert ALLOWED_TRANSITIONS[S.ARCHIVED] == frozenset()


class TestStatusUpdateRequest:
    def test_ready_requires_artifact_uri(self) -> None:
        with pytest.raises(ValidationError, match="artifact_uri is required"):
            ModelVersionStatusUpdateRequest(status=S.READY)

    @pytest.mark.parametrize("status", [S.TRAINING, S.FAILED, S.ARCHIVED])
    def test_artifact_uri_is_rejected_for_other_statuses(self, status: S) -> None:
        with pytest.raises(ValidationError, match="only be set when status is READY"):
            ModelVersionStatusUpdateRequest(status=status, artifact_uri="s3://bucket/a/")

    @pytest.mark.parametrize(
        "uri", ["", "   ", "models/a/versions/b/", "/local/path", "s3://", "s3://bucket/a b/"]
    )
    def test_artifact_uri_must_be_absolute_uri(self, uri: str) -> None:
        with pytest.raises(ValidationError):
            ModelVersionStatusUpdateRequest(status=S.READY, artifact_uri=uri)

    def test_artifact_uri_length_is_limited(self) -> None:
        with pytest.raises(ValidationError):
            ModelVersionStatusUpdateRequest(
                status=S.READY, artifact_uri="s3://bucket/" + "a" * 2048
            )

    def test_artifact_uri_is_stripped(self) -> None:
        request = ModelVersionStatusUpdateRequest(status=S.READY, artifact_uri=" gs://b/x/ ")

        assert request.artifact_uri == "gs://b/x/"

    def test_undefined_status_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelVersionStatusUpdateRequest(status="DONE")  # type: ignore[arg-type]


class TestArtifactPath:
    model_id = uuid.uuid4()
    version_id = uuid.uuid4()

    @pytest.mark.parametrize(
        "template",
        [
            "s3://model-artifacts/models/{m}/versions/{v}/model.tar.gz",
            "gs://other-bucket/prefix/models/{m}/versions/{v}/",
        ],
    )
    def test_path_with_model_and_version_uuid_is_accepted(self, template: str) -> None:
        uri = template.format(m=self.model_id, v=self.version_id)

        _check_artifact_path(uri, self.model_id, self.version_id)

    @pytest.mark.parametrize(
        "template",
        [
            # version_no 기반 경로는 롤백이나 재시도 때 다른 버전의 아티팩트를 덮어쓴다.
            "s3://bucket/models/{m}/versions/1/",
            "s3://bucket/models/{m}/versions/{other}/",
            "s3://bucket/models/{other}/versions/{v}/",
            "s3://bucket/artifacts/{v}/",
            # 버킷 이름이 아니라 경로에 있어야 한다.
            "s3://models/{m}/versions/{v}/",
        ],
    )
    def test_path_not_owned_by_version_is_rejected(self, template: str) -> None:
        uri = template.format(m=self.model_id, v=self.version_id, other=uuid.uuid4())

        with pytest.raises(AppError) as caught:
            _check_artifact_path(uri, self.model_id, self.version_id)

        assert caught.value.details[0]["location"] == ["body", "artifact_uri"]
