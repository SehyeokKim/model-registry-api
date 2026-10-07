"""복원 기한이 지난 모델 영구 삭제 커맨드 통합 테스트 (ADR 0005)."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.entities import MLModel, MLModelVersion, TaskType
from scripts.purge_expired_models import main, run
from scripts.seed import ORGANIZATION_B_ID, seed_demo_data
from tests.support import CUSTOMER_A1, CUSTOMER_B1, ORGANIZATION_A, insert_version


@pytest.fixture(autouse=True)
def _demo_data(engine: Engine) -> None:
    seed_demo_data(engine)


def _add_model(
    engine: Engine,
    name: str,
    *,
    deleted_days_ago: int | None = None,
    restore_window_days: int = 30,
    organization_id: uuid.UUID = ORGANIZATION_A,
    created_by: uuid.UUID = CUSTOMER_A1,
) -> str:
    """삭제 시각을 과거로 두고 모델을 넣는다. deleted_days_ago가 없으면 활성 모델이다."""
    model = MLModel(
        organization_id=organization_id,
        created_by=created_by,
        name=name,
        task_type=TaskType.CLASSIFICATION,
    )
    if deleted_days_ago is not None:
        model.deleted_at = func.now() - timedelta(days=deleted_days_ago)
        model.purge_at = func.now() - timedelta(days=deleted_days_ago - restore_window_days)
    with Session(engine) as session, session.begin():
        session.add(model)
        session.flush()
        return str(model.id)


def _expired(engine: Engine, name: str, **kwargs: object) -> str:
    return _add_model(engine, name, deleted_days_ago=31, **kwargs)  # type: ignore[arg-type]


def _remaining_names(engine: Engine) -> list[str]:
    with Session(engine) as session:
        return sorted(session.scalars(select(MLModel.name)))


def _version_count(engine: Engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(MLModelVersion)) or 0


def test_purges_only_models_past_restore_deadline(engine: Engine, settings: Settings) -> None:
    _add_model(engine, "active")
    _add_model(engine, "restorable", deleted_days_ago=1)
    _expired(engine, "expired-a")
    _expired(engine, "expired-b", organization_id=ORGANIZATION_B_ID, created_by=CUSTOMER_B1)

    deleted = run(settings, dry_run=False)

    assert deleted == 2
    assert _remaining_names(engine) == ["active", "restorable"]


def test_versions_are_deleted_with_model(engine: Engine, settings: Settings) -> None:
    expired = _expired(engine, "expired")
    kept = _add_model(engine, "active")
    insert_version(engine, expired, 1)
    insert_version(engine, expired, 2)
    insert_version(engine, kept, 1)

    run(settings, dry_run=False)

    assert _version_count(engine) == 1


def test_deletes_in_batches_until_none_left(
    engine: Engine, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    for i in range(5):
        _expired(engine, f"expired-{i}")

    with caplog.at_level("INFO", logger="app.services.model_purge_service"):
        deleted = run(settings.model_copy(update={"purge_batch_size": 2}), dry_run=False)

    batches = [r for r in caplog.records if r.message == "Expired models purged"]
    assert deleted == 5
    assert [r.deleted_count for r in batches] == [2, 2, 1]  # type: ignore[attr-defined]
    assert sum(len(r.model_ids) for r in batches) == 5  # type: ignore[attr-defined]
    assert batches[0].organization_ids == [str(ORGANIZATION_A)]  # type: ignore[attr-defined]
    assert _remaining_names(engine) == []


def test_dry_run_deletes_nothing(
    engine: Engine, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    _expired(engine, "expired-a")
    _expired(engine, "expired-b")
    _add_model(engine, "restorable", deleted_days_ago=1)

    with caplog.at_level("INFO", logger="scripts.purge_expired_models"):
        found = run(settings, dry_run=True)

    assert found == 2
    record = next(r for r in caplog.records if r.message == "Dry run: expired models found")
    assert record.expired_count == 2  # type: ignore[attr-defined]
    assert len(_remaining_names(engine)) == 3


def test_nothing_to_purge(engine: Engine, settings: Settings) -> None:
    _add_model(engine, "active")

    assert run(settings, dry_run=False) == 0


def test_skips_model_locked_by_another_transaction(engine: Engine, settings: Settings) -> None:
    """복원 트랜잭션이 잠근 모델은 기다리지 않고 건너뛰고, 다음 실행에서 처리한다."""
    locked = _expired(engine, "locked")
    _expired(engine, "free")

    executor = ThreadPoolExecutor(max_workers=1)
    with Session(engine) as session:
        session.begin()
        # 복원과 같은 잠금 모드로 잠근 채 영구 삭제를 실행한다.
        session.execute(
            select(MLModel).where(MLModel.id == uuid.UUID(locked)).with_for_update(key_share=True)
        )
        future = executor.submit(run, settings, dry_run=False)
        try:
            # 건너뛰지 않고 잠금을 기다리면 여기서 시간 초과로 실패한다.
            deleted = future.result(timeout=10)
        finally:
            # 실패해도 잠금을 풀어 기다리던 작업이 끝나게 한다(교착 방지).
            session.rollback()
            executor.shutdown()

    assert deleted == 1
    assert _remaining_names(engine) == ["locked"]
    assert run(settings, dry_run=False) == 1


def test_main_accepts_dry_run_flag(
    engine: Engine, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _expired(engine, "expired")
    monkeypatch.setattr("scripts.purge_expired_models.get_settings", lambda: settings)
    monkeypatch.setattr("scripts.purge_expired_models.configure_logging", lambda *_: None)

    main(["--dry-run"])
    assert _remaining_names(engine) == ["expired"]

    main([])
    assert _remaining_names(engine) == []
