"""모델 유스케이스. 유스케이스 하나가 트랜잭션 하나다.

쓰기 유스케이스는 모델 행을 FOR NO KEY UPDATE로 잠근 뒤 규칙을 확인하고, 단건 응답이 바뀌므로
row_version을 1 올린다 (ADR 0003, 0007).
"""

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ErrorCode
from app.core.exceptions import AppError
from app.db.entities import MLModel, ModelStatusFilter, TaskType
from app.db.errors import violated_constraint
from app.repositories.model_repository import ListPosition, ModelRepository
from app.services.requester import Requester

ACTIVE_NAME_CONSTRAINT = "uq_models_organization_id_name_active"
UPDATABLE_FIELDS = frozenset({"name", "description", "task_type"})


@dataclass(frozen=True, slots=True)
class ModelPage:
    """목록 한 페이지. next_position이 None이면 마지막 페이지다."""

    items: Sequence[Row[Any]]
    next_position: ListPosition | None


class ModelService:
    def __init__(self, session: Session, *, restore_window_days: int) -> None:
        self._session = session
        self._models = ModelRepository(session)
        self._restore_window = timedelta(days=restore_window_days)

    def create(
        self, requester: Requester, *, name: str, task_type: TaskType, description: str | None
    ) -> MLModel:
        model = MLModel(
            organization_id=requester.organization_id,
            created_by=requester.user_id,
            name=name,
            task_type=task_type,
            description=description,
            versions=[],
        )
        # 이름 중복은 미리 조회하지 않고 unique 인덱스 위반으로 판단한다.
        # 동시에 같은 이름으로 생성해도 하나만 성공한다.
        with _translate_name_conflict(), self._session.begin():
            self._models.add(model)
        return model

    def get(self, requester: Requester, model_id: uuid.UUID) -> MLModel:
        with self._session.begin():
            return self._get_active(requester, model_id)

    def list_page(
        self,
        requester: Requester,
        *,
        status: ModelStatusFilter,
        limit: int,
        after: ListPosition | None,
    ) -> ModelPage:
        # 한 건을 더 읽어 다음 페이지가 있는지 판단한다. 전체 건수(COUNT)는 세지 않는다.
        with self._session.begin():
            rows = self._models.list_page(
                requester.organization_id, status=status, limit=limit + 1, after=after
            )
        if len(rows) <= limit:
            return ModelPage(items=rows, next_position=None)
        items = rows[:limit]
        last = items[-1]
        return ModelPage(items=items, next_position=(last.created_at, last.id))

    def update(self, requester: Requester, model_id: uuid.UUID, changes: dict[str, Any]) -> MLModel:
        """`changes`에는 클라이언트가 실제로 보낸 필드만 담는다 (JSON Merge Patch)."""
        if not changes:
            raise AppError(ErrorCode.EMPTY_UPDATE)
        unknown = changes.keys() - UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"Unsupported fields: {sorted(unknown)}")

        with _translate_name_conflict(), self._session.begin():
            model = self._get_active(requester, model_id, for_update=True)
            new_task_type = changes.get("task_type", model.task_type)
            # 버전 목록은 잠금 안에서 읽었으므로, 확인 직후 버전이 생기는 경쟁이 없다.
            if new_task_type != model.task_type and model.versions:
                raise AppError(ErrorCode.TASK_TYPE_LOCKED)

            for field, value in changes.items():
                setattr(model, field, value)
            self._touch(model)
            self._models.flush()
        return model

    def delete(self, requester: Requester, model_id: uuid.UUID) -> MLModel:
        """소프트 삭제. purge_at까지 복원할 수 있고, 이후 영구 삭제 배치가 지운다 (ADR 0005)."""
        with self._session.begin():
            model = self._get_active(requester, model_id, for_update=True)
            now = self._touch(model)
            model.deleted_at = now
            model.purge_at = now + self._restore_window
            self._models.flush()
        return model

    def restore(self, requester: Requester, model_id: uuid.UUID) -> MLModel:
        """복원 기한(purge_at) 전의 삭제된 모델을 되살린다.

        기한이 지난 모델은 영구 삭제 대기 중일 뿐 삭제가 확정되었으므로 복원하지 않는다 (ADR 0005).
        같은 이름의 활성 모델이 있으면 이름을 바꾸지 않고 충돌로 알린다 (ADR 0009).
        """
        with _translate_name_conflict(), self._session.begin():
            model = self._models.get(
                requester.organization_id, model_id, include_deleted=True, for_update=True
            )
            if model is None:
                raise AppError(ErrorCode.MODEL_NOT_FOUND)
            if model.deleted_at is None or model.purge_at is None:
                raise AppError(ErrorCode.MODEL_NOT_DELETED)
            if model.purge_at <= self._models.current_timestamp():
                raise AppError(ErrorCode.MODEL_PURGED)

            model.deleted_at = None
            model.purge_at = None
            self._touch(model)
            # 활성 이름 unique 인덱스 위반은 여기서 드러난다.
            self._models.flush()
        return model

    def _get_active(
        self, requester: Requester, model_id: uuid.UUID, *, for_update: bool = False
    ) -> MLModel:
        model = self._models.get(requester.organization_id, model_id, for_update=for_update)
        if model is None:
            raise AppError(ErrorCode.MODEL_NOT_FOUND)
        return model

    def _touch(self, model: MLModel) -> datetime:
        """쓰기 공통 처리: DB 시각으로 updated_at을 갱신하고 row_version을 올린다."""
        now = self._models.current_timestamp()
        model.updated_at = now
        model.row_version += 1
        return now


@contextmanager
def _translate_name_conflict() -> Iterator[None]:
    """활성 이름 unique 제약 위반을 MODEL_NAME_CONFLICT로 바꾼다. 다른 무결성 오류는 그대로 올린다.

    트랜잭션 블록보다 바깥에 두어, 롤백이 끝난 뒤 도메인 예외로 바꾼다.
    """
    try:
        yield
    except IntegrityError as error:
        if violated_constraint(error) == ACTIVE_NAME_CONSTRAINT:
            raise AppError(ErrorCode.MODEL_NAME_CONFLICT) from error
        raise
