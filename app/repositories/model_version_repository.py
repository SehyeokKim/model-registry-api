"""모델 버전 조회와 저장.

버전은 모델에 속하므로 model_id로만 접근한다. 고객 API에서는 호출자가 먼저 요청자 조직의
모델인지 확인하고 모델 행을 잠근 뒤 이 함수들을 부른다 (ADR 0003, 0006).
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.entities import MLModelVersion


class ModelVersionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, version: MLModelVersion) -> None:
        """INSERT를 바로 실행해 제약 위반을 호출 지점에서 드러내고, 서버 기본값을 채운다."""
        self._session.add(version)
        self._session.flush()

    def next_version_no(self, model_id: uuid.UUID) -> int:
        """다음 버전 번호. 모델 행을 잠근 트랜잭션 안에서만 호출한다 (ADR 0003).

        (model_id, version_no) unique 인덱스로 최댓값을 바로 찾는다.
        """
        statement = select(func.coalesce(func.max(MLModelVersion.version_no), 0) + 1).where(
            MLModelVersion.model_id == model_id
        )
        return self._session.execute(statement).scalar_one()

    def get_in_model(self, model_id: uuid.UUID, version_id: uuid.UUID) -> MLModelVersion | None:
        """모델에 속한 버전. 다른 모델의 버전 ID면 None이다."""
        return self._session.scalar(
            select(MLModelVersion).where(
                MLModelVersion.id == version_id, MLModelVersion.model_id == model_id
            )
        )
