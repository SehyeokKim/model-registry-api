"""모델 버전 유스케이스. 유스케이스 하나가 트랜잭션 하나다."""

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ErrorCode
from app.core.exceptions import AppError
from app.db.entities import MLModelVersion, ModelVersionStatus
from app.db.errors import violated_constraint
from app.repositories.model_repository import ModelRepository
from app.repositories.model_version_repository import ModelVersionRepository
from app.services.model_service import get_active_model, touch_model
from app.services.requester import Requester

VERSION_NO_CONSTRAINT = "uq_model_versions_model_id_version_no"

# 허용하는 상태 전이. FAILED, ARCHIVED는 최종 상태이며, 다시 학습하려면 새 버전을 만든다.
ALLOWED_TRANSITIONS: dict[ModelVersionStatus, frozenset[ModelVersionStatus]] = {
    ModelVersionStatus.PENDING: frozenset({ModelVersionStatus.TRAINING, ModelVersionStatus.FAILED}),
    ModelVersionStatus.TRAINING: frozenset({ModelVersionStatus.READY, ModelVersionStatus.FAILED}),
    ModelVersionStatus.READY: frozenset({ModelVersionStatus.ARCHIVED}),
    ModelVersionStatus.FAILED: frozenset(),
    ModelVersionStatus.ARCHIVED: frozenset(),
}

logger = logging.getLogger(__name__)


class ModelVersionService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._models = ModelRepository(session)
        self._versions = ModelVersionRepository(session)

    def create(
        self,
        requester: Requester,
        model_id: uuid.UUID,
        *,
        parent_version_id: uuid.UUID | None,
        framework: str | None,
        input_schema: dict[str, Any] | None,
        output_schema: dict[str, Any] | None,
    ) -> MLModelVersion:
        """새 버전을 PENDING 상태로 만든다. 번호는 모델별로 1부터 순차 증가한다.

        모델 행을 잠근 뒤 번호를 구하므로 동시에 생성해도 번호가 겹치지 않는다 (ADR 0003).
        이전 버전이 학습 중이어도 새 버전을 만들 수 있다.
        """
        with _translate_version_conflict(), self._session.begin():
            model = get_active_model(
                self._models, requester, model_id, for_update=True, load_versions=False
            )
            if parent_version_id is not None:
                parent = self._versions.get_in_model(model.id, parent_version_id)
                # 재학습의 기준은 학습이 끝난 버전이어야 계보와 재현성이 성립한다.
                if parent is None or parent.status != ModelVersionStatus.READY:
                    raise AppError(ErrorCode.INVALID_PARENT_VERSION)

            version = MLModelVersion(
                model_id=model.id,
                version_no=self._versions.next_version_no(model.id),
                parent_version_id=parent_version_id,
                status=ModelVersionStatus.PENDING,
                framework=framework,
                input_schema=input_schema,
                output_schema=output_schema,
                created_by=requester.user_id,
            )
            self._versions.add(version)
            # 모델 단건 응답의 버전 목록이 바뀌므로 모델의 row_version을 올린다 (ADR 0007).
            touch_model(self._models, model)
            self._models.flush()
        return version

    def change_status(
        self,
        version_id: uuid.UUID,
        *,
        status: ModelVersionStatus,
        artifact_uri: str | None,
    ) -> MLModelVersion:
        """학습 파이프라인이 버전 상태를 바꾼다 (내부 API, 조직 범위 제한 없음).

        소프트 삭제된 모델의 버전도 바꿀 수 있다. 삭제 전에 시작한 학습 결과를 기록해야 복원 후
        상태가 정확하다. 복원 기한이 지난 모델은 영구 삭제된 것으로 보고 버전도 없는 것으로 다룬다.
        """
        with self._session.begin():
            owner = self._versions.get_owner(version_id)
            if owner is None:
                raise AppError(ErrorCode.MODEL_VERSION_NOT_FOUND)
            model_id, organization_id = owner
            # 버전 상태도 모델 단건 응답에 포함되므로 다른 쓰기와 같은 모델 행 잠금으로 직렬화한다.
            model = self._models.get(
                organization_id,
                model_id,
                include_deleted=True,
                for_update=True,
                load_versions=False,
            )
            now = self._models.current_timestamp()
            if model is None or (model.purge_at is not None and model.purge_at <= now):
                raise AppError(ErrorCode.MODEL_VERSION_NOT_FOUND)
            # 잠금을 잡은 뒤 읽어야 동시에 들어온 다른 전이의 결과를 본다.
            version = self._versions.get_in_model(model_id, version_id, with_schemas=True)
            if version is None:
                raise AppError(ErrorCode.MODEL_VERSION_NOT_FOUND)

            previous = version.status
            if status not in ALLOWED_TRANSITIONS[previous]:
                raise AppError(
                    ErrorCode.INVALID_STATUS_TRANSITION,
                    f"Cannot change status from {previous} to {status}.",
                )
            if artifact_uri is not None:
                _check_artifact_path(artifact_uri, model_id, version_id)
                version.artifact_uri = artifact_uri
            version.status = status
            touch_model(self._models, model)
            self._models.flush()

        logger.info(
            "Model version status changed",
            extra={
                "model_id": str(model_id),
                "version_id": str(version_id),
                "from_status": previous.value,
                "to_status": status.value,
            },
        )
        return version


def _check_artifact_path(artifact_uri: str, model_id: uuid.UUID, version_id: uuid.UUID) -> None:
    """아티팩트 경로는 버전 UUID로만 만든다 (ADR 0003).

    경로에 models/{model_id}/versions/{version_id}/가 있어야 다른 버전의 아티팩트를 덮어쓰지 않고,
    영구 삭제 때 모델 ID로 아티팩트를 빠짐없이 찾아 파기할 수 있다 (ADR 0005).
    버킷과 스킴, `models/` 앞의 접두 경로는 제한하지 않는다.
    `..` 세그먼트나 앞에 다른 `models/` 경로를 넣어 규칙을 우회하지 못하게, 경로의 첫 `models`
    세그먼트 바로 뒤가 이 모델과 버전이어야 한다.
    """
    expected = f"models/{model_id}/versions/{version_id}/"
    segments = urlsplit(artifact_uri).path.split("/")
    if any(segment in (".", "..") for segment in segments):
        _raise_artifact_uri_error("artifact_uri path must not contain '.' or '..' segments")
    start = segments.index("models") if "models" in segments else -1
    owned = segments[start + 1 : start + 4] == [str(model_id), "versions", str(version_id)]
    if start < 0 or not owned or len(segments) <= start + 4:
        _raise_artifact_uri_error(f"artifact_uri path must contain '{expected}'")


def _raise_artifact_uri_error(message: str) -> None:
    raise AppError(
        ErrorCode.VALIDATION_ERROR,
        details=[{"location": ["body", "artifact_uri"], "message": message, "type": "value_error"}],
    )


@contextmanager
def _translate_version_conflict() -> Iterator[None]:
    """버전 번호 unique 제약 위반을 MODEL_VERSION_CONFLICT로 바꾼다.

    모델 행 잠금으로 직렬화하므로 정상 경로에서는 일어나지 않는다. 잠금 규칙을 따르지 않은
    다른 경로가 버전을 넣은 경우의 최종 방어선이다.
    """
    try:
        yield
    except IntegrityError as error:
        if violated_constraint(error) == VERSION_NO_CONSTRAINT:
            raise AppError(ErrorCode.MODEL_VERSION_CONFLICT) from error
        raise
