"""복원 기한(purge_at)이 지난 모델을 영구 삭제한다 (ADR 0005).

운영에서는 Kubernetes CronJob(concurrencyPolicy: Forbid)으로 매일 실행하고,
로컬에서는 docker compose로 직접 실행한다.

    python -m scripts.purge_expired_models --dry-run   # 대상 건수만 확인
    python -m scripts.purge_expired_models

DB 오류가 나면 예외로 끝나 0이 아닌 종료 코드를 돌려주고, CronJob이 재시도한다.
"""

import argparse
import logging
from collections.abc import Sequence

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import create_db_engine, create_session_factory
from app.services.model_purge_service import ModelPurgeService

logger = logging.getLogger("scripts.purge_expired_models")


def run(settings: Settings, *, dry_run: bool) -> int:
    """영구 삭제한(dry-run이면 대상인) 모델 수를 돌려준다."""
    engine = create_db_engine(settings)
    try:
        service = ModelPurgeService(
            create_session_factory(engine), batch_size=settings.purge_batch_size
        )
        if dry_run:
            expired = service.count_expired()
            logger.info("Dry run: expired models found", extra={"expired_count": expired})
            return expired

        result = service.purge()
        logger.info(
            "Purge completed",
            extra={"deleted_count": result.deleted, "batches": result.batches},
        )
        return result.deleted
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="복원 기한이 지난 모델을 영구 삭제한다.")
    parser.add_argument(
        "--dry-run", action="store_true", help="삭제하지 않고 대상 건수만 로그로 남긴다"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    run(settings, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
