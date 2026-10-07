"""복원 기한이 지난 모델의 영구 삭제 (ADR 0005).

조직을 가로지르는 시스템 배치라 organization_id 조건이 없다. 이 모듈은 영구 삭제 커맨드에서만 쓰고,
API 경로에서는 쓰지 않는다. 고객에게 데이터를 돌려주지 않으므로 조직 범위 누락으로 인한 노출이 없다.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.orm import Session

from app.db.entities import MLModel


def _expired() -> ColumnElement[bool]:
    """복원 기한이 지난 모델. 다른 API와 같은 경계(DB 시각 purge_at <= now())를 쓴다."""
    return MLModel.deleted_at.is_not(None) & (MLModel.purge_at <= func.now())


class ModelPurgeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def count_expired(self) -> int:
        statement = select(func.count()).select_from(MLModel).where(_expired())
        return self._session.execute(statement).scalar_one()

    def delete_expired_batch(self, limit: int) -> Sequence[tuple[uuid.UUID, uuid.UUID]]:
        """기한이 오래 지난 순으로 최대 limit건을 지우고 (id, organization_id)를 돌려준다.

        다른 트랜잭션(복원 등)이 잠근 행은 기다리지 않고 건너뛴다. 다음 실행에서 처리한다.
        model_versions는 FK ON DELETE CASCADE로 함께 삭제된다.
        ix_models_purge_at 부분 인덱스(WHERE deleted_at IS NOT NULL)로 대상을 찾는다.
        """
        targets = (
            select(MLModel.id)
            .where(_expired())
            .order_by(MLModel.purge_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        statement = (
            delete(MLModel)
            .where(MLModel.id.in_(targets))
            .returning(MLModel.id, MLModel.organization_id)
        )
        rows = self._session.execute(statement).all()
        return [(row.id, row.organization_id) for row in rows]
