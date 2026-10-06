"""모델 조회와 저장.

조직 소유 데이터이므로 조회 함수는 organization_id를 필수로 받는다 (ADR 0006).
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.entities import MLModel


class ModelRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, model: MLModel) -> None:
        """INSERT를 바로 실행해 제약 위반을 호출 지점에서 드러내고, 서버 기본값을 채운다."""
        self._session.add(model)
        self._session.flush()

    def flush(self) -> None:
        self._session.flush()

    def get_active(
        self, organization_id: uuid.UUID, model_id: uuid.UUID, *, for_update: bool = False
    ) -> MLModel | None:
        """조직의 활성(삭제되지 않은) 모델을 버전 목록과 함께 가져온다.

        for_update=True면 모델 행을 FOR NO KEY UPDATE로 잠근다. 같은 모델에 대한 수정, 삭제,
        복원, 버전 생성이 이 잠금에서 직렬화된다 (ADR 0003).
        """
        statement = (
            select(MLModel)
            .where(
                MLModel.id == model_id,
                MLModel.organization_id == organization_id,
                MLModel.deleted_at.is_(None),
            )
            .options(selectinload(MLModel.versions))
        )
        if for_update:
            statement = statement.with_for_update(key_share=True, of=MLModel)
        return self._session.scalar(statement)

    def current_timestamp(self) -> datetime:
        """DB 서버 시각. 트랜잭션 안에서는 같은 값을 돌려주므로 한 유스케이스의 시각이 일관된다."""
        return self._session.execute(select(func.now())).scalar_one()
