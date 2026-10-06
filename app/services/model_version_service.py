"""모델 버전 유스케이스. 유스케이스 하나가 트랜잭션 하나다."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

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
