"""모델 조회와 저장.

조직 소유 데이터이므로 조회 함수는 organization_id를 필수로 받는다 (ADR 0006).
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Row, func, or_, select, true, tuple_
from sqlalchemy.orm import Session, selectinload

from app.db.entities import MLModel, MLModelVersion, ModelStatusFilter

# 목록 조회 keyset 위치: (created_at, id)
ListPosition = tuple[datetime, uuid.UUID]


class ModelRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, model: MLModel) -> None:
        """INSERT를 바로 실행해 제약 위반을 호출 지점에서 드러내고, 서버 기본값을 채운다."""
        self._session.add(model)
        self._session.flush()

    def flush(self) -> None:
        self._session.flush()

    def get(
        self,
        organization_id: uuid.UUID,
        model_id: uuid.UUID,
        *,
        include_deleted: bool = False,
        for_update: bool = False,
    ) -> MLModel | None:
        """조직의 모델을 버전 목록과 함께 가져온다. 기본은 활성(삭제되지 않은) 모델만 찾는다.

        for_update=True면 모델 행을 FOR NO KEY UPDATE로 잠근다. 같은 모델에 대한 수정, 삭제,
        복원, 버전 생성이 이 잠금에서 직렬화된다 (ADR 0003).
        """
        statement = (
            select(MLModel)
            .where(MLModel.id == model_id, MLModel.organization_id == organization_id)
            .options(selectinload(MLModel.versions))
        )
        if not include_deleted:
            statement = statement.where(MLModel.deleted_at.is_(None))
        if for_update:
            statement = statement.with_for_update(key_share=True, of=MLModel)
        return self._session.scalar(statement)

    def list_page(
        self,
        organization_id: uuid.UUID,
        *,
        status: ModelStatusFilter,
        limit: int,
        after: ListPosition | None,
    ) -> Sequence[Row[Any]]:
        """조직의 모델을 최신 생성 순으로 최대 limit건 가져온다 (keyset 페이지네이션).

        각 행은 models 컬럼과 버전 집계(version_count, latest_version_no)를 함께 담는다.
        ORDER BY created_at DESC, id DESC는 ix_models_organization_id_created_at_id를
        역방향으로 읽으므로, 페이지 위치와 관계없이 limit건만 읽고 멈춘다.
        """
        # LATERAL: 페이지에 들어가는 모델마다 (model_id, version_no) unique 인덱스로 집계한다.
        # 전체 버전을 GROUP BY한 뒤 조인하면 페이지 크기와 관계없이 모든 버전을 읽게 된다.
        # 집계 함수만 있는 SELECT는 버전이 없어도 한 행(count=0, max=NULL)을 돌려준다.
        version_stats = (
            select(
                func.count().label("version_count"),
                func.max(MLModelVersion.version_no).label("latest_version_no"),
            )
            .where(MLModelVersion.model_id == MLModel.id)
            .lateral("version_stats")
        )
        statement = (
            select(
                *MLModel.__table__.columns,
                version_stats.c.version_count,
                version_stats.c.latest_version_no,
            )
            .select_from(MLModel)
            .join(version_stats, true())
            .where(MLModel.organization_id == organization_id, _status_condition(status))
            .order_by(MLModel.created_at.desc(), MLModel.id.desc())
            .limit(limit)
        )
        if after is not None:
            statement = statement.where(tuple_(MLModel.created_at, MLModel.id) < tuple_(*after))
        return self._session.execute(statement).all()

    def current_timestamp(self) -> datetime:
        """DB 서버 시각. 트랜잭션 안에서는 같은 값을 돌려주므로 한 유스케이스의 시각이 일관된다."""
        return self._session.execute(select(func.now())).scalar_one()


def _status_condition(status: ModelStatusFilter) -> ColumnElement[bool]:
    """복원 기한이 지난 모델(purge_at <= now())은 삭제가 확정된 것으로 보고 제외한다."""
    active = MLModel.deleted_at.is_(None)
    restorable = MLModel.purge_at > func.now()
    match status:
        case ModelStatusFilter.ACTIVE:
            return active
        case ModelStatusFilter.DELETED:
            return MLModel.deleted_at.is_not(None) & restorable
        case ModelStatusFilter.ALL:
            return or_(active, restorable)
