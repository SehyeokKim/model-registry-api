"""복원 기한이 지난 모델 영구 삭제 유스케이스 (ADR 0005).

배치 하나가 트랜잭션 하나다. 트랜잭션을 짧게 끊어 잠금 시간과 WAL 급증을 줄이고,
중간에 실패해도 이미 커밋한 배치는 남는다. 다음 실행이 나머지를 이어서 처리한다.
"""

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.repositories.model_purge_repository import ModelPurgeRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PurgeResult:
    deleted: int
    batches: int


class ModelPurgeService:
    def __init__(self, session_factory: sessionmaker[Session], *, batch_size: int) -> None:
        self._session_factory = session_factory
        self._batch_size = batch_size

    def count_expired(self) -> int:
        with self._session_factory() as session, session.begin():
            return ModelPurgeRepository(session).count_expired()

    def purge(self) -> PurgeResult:
        """지울 행이 없을 때까지 배치를 반복한다.

        다른 트랜잭션이 잠가 건너뛴 행은 이번 실행에서 다시 시도하지 않는다. 잠긴 행 때문에
        같은 배치를 반복하지 않도록, 빈 배치가 나오면 끝낸다.
        """
        deleted = 0
        batches = 0
        while True:
            with self._session_factory() as session, session.begin():
                rows = ModelPurgeRepository(session).delete_expired_batch(self._batch_size)
            if not rows:
                break
            batches += 1
            deleted += len(rows)
            # 감사 기록이자 오브젝트 스토리지 아티팩트 정리(models/{model_id}/...)의 입력이다.
            logger.info(
                "Expired models purged",
                extra={
                    "batch": batches,
                    "deleted_count": len(rows),
                    "model_ids": [str(model_id) for model_id, _ in rows],
                    "organization_ids": sorted({str(org_id) for _, org_id in rows}),
                },
            )
        return PurgeResult(deleted=deleted, batches=batches)
