"""버전 상태 변경 내부 API 통합 테스트."""

import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, update
from sqlalchemy.orm import Session

from app.db.entities import MLModel, ModelVersionStatus, User, UserRole
from scripts.seed import seed_demo_data
from tests.support import (
    CUSTOMER_A1,
    CUSTOMER_B1,
    ML_ENGINEER,
    as_user,
    insert_version,
)

MODELS = "/api/v1/models"
ADMIN = uuid.UUID("00000000-0000-0000-0000-0000000000ad")


@pytest.fixture(autouse=True)
def _demo_data(engine: Engine) -> None:
    seed_demo_data(engine)
    with Session(engine) as session, session.begin():
        session.add(User(id=ADMIN, email="admin@registry.test", name="Admin", role=UserRole.ADMIN))


@pytest.fixture
def model(create_model: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    return create_model()


def _status_path(version_id: str) -> str:
    return f"/internal/v1/model-versions/{version_id}/status"


def _change(client: TestClient, version_id: str, user: uuid.UUID = ML_ENGINEER, **body: Any) -> Any:
    return client.patch(_status_path(version_id), json=body, headers=as_user(user))


def _artifact_uri(model_id: str, version_id: str) -> str:
    return f"s3://model-artifacts/models/{model_id}/versions/{version_id}/model.tar.gz"


def _error_code(response: Any) -> str:
    return response.json()["error"]["code"]


def _row_version(engine: Engine, model_id: str) -> int:
    with Session(engine) as session:
        return session.get_one(MLModel, uuid.UUID(model_id)).row_version


class TestTransitions:
    def test_full_training_lifecycle(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        version_id = insert_version(engine, model["id"])
        uri = _artifact_uri(model["id"], version_id)

        training = _change(client, version_id, status="TRAINING")
        ready = _change(client, version_id, status="READY", artifact_uri=uri)
        archived = _change(client, version_id, status="ARCHIVED")

        assert [r.status_code for r in (training, ready, archived)] == [200, 200, 200]
        assert ready.json()["status"] == "READY"
        assert ready.json()["artifact_uri"] == uri
        assert archived.json()["artifact_uri"] == uri
        assert archived.json()["status"] == "ARCHIVED"
        # 생성 1 + 상태 변경 3
        assert _row_version(engine, model["id"]) == 4

    @pytest.mark.parametrize("current", [ModelVersionStatus.PENDING, ModelVersionStatus.TRAINING])
    def test_can_fail_before_or_during_training(
        self,
        client: TestClient,
        engine: Engine,
        model: dict[str, Any],
        current: ModelVersionStatus,
    ) -> None:
        version_id = insert_version(engine, model["id"], status=current)

        response = _change(client, version_id, status="FAILED")

        assert response.status_code == 200
        assert response.json()["status"] == "FAILED"

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (ModelVersionStatus.PENDING, "READY"),
            (ModelVersionStatus.PENDING, "PENDING"),
            (ModelVersionStatus.TRAINING, "TRAINING"),
            (ModelVersionStatus.READY, "TRAINING"),
            (ModelVersionStatus.FAILED, "TRAINING"),
            (ModelVersionStatus.ARCHIVED, "READY"),
        ],
    )
    def test_disallowed_transition_is_conflict(
        self,
        client: TestClient,
        engine: Engine,
        model: dict[str, Any],
        current: ModelVersionStatus,
        target: str,
    ) -> None:
        version_id = insert_version(engine, model["id"], status=current)
        body: dict[str, Any] = {"status": target}
        if target == "READY":
            body["artifact_uri"] = _artifact_uri(model["id"], version_id)

        response = _change(client, version_id, **body)

        assert response.status_code == 409
        assert _error_code(response) == "INVALID_STATUS_TRANSITION"
        assert _row_version(engine, model["id"]) == 1

    def test_response_includes_version_schemas(
        self, client: TestClient, model: dict[str, Any]
    ) -> None:
        schema = {"type": "object"}
        created = client.post(
            f"{MODELS}/{model['id']}/versions",
            json={"input_schema": schema},
            headers=as_user(CUSTOMER_A1),
        ).json()

        response = _change(client, created["id"], status="TRAINING")

        assert response.json()["input_schema"] == schema

    def test_ready_version_can_be_used_as_parent(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        version_id = insert_version(engine, model["id"], status=ModelVersionStatus.TRAINING)
        _change(
            client,
            version_id,
            status="READY",
            artifact_uri=_artifact_uri(model["id"], version_id),
        )

        response = client.post(
            f"{MODELS}/{model['id']}/versions",
            json={"parent_version_id": version_id},
            headers=as_user(CUSTOMER_A1),
        )

        assert response.status_code == 201


class TestArtifactUri:
    def test_ready_without_artifact_uri_is_validation_error(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        version_id = insert_version(engine, model["id"], status=ModelVersionStatus.TRAINING)

        response = _change(client, version_id, status="READY")

        assert response.status_code == 422
        assert _error_code(response) == "VALIDATION_ERROR"

    def test_artifact_path_of_other_version_is_rejected(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        version_id = insert_version(engine, model["id"], 1, ModelVersionStatus.TRAINING)
        other_id = insert_version(engine, model["id"], 2, ModelVersionStatus.TRAINING)

        response = _change(
            client,
            version_id,
            status="READY",
            artifact_uri=_artifact_uri(model["id"], other_id),
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"][0]["location"] == ["body", "artifact_uri"]
        assert _row_version(engine, model["id"]) == 1


class TestAccess:
    def test_customer_cannot_call_internal_api(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        version_id = insert_version(engine, model["id"])

        response = _change(client, version_id, user=CUSTOMER_A1, status="TRAINING")

        assert response.status_code == 403
        assert _error_code(response) == "FORBIDDEN"

    def test_admin_can_call_internal_api(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        version_id = insert_version(engine, model["id"])

        assert _change(client, version_id, user=ADMIN, status="TRAINING").status_code == 200

    def test_internal_user_is_not_limited_to_an_organization(
        self,
        client: TestClient,
        engine: Engine,
        create_model: Callable[..., dict[str, Any]],
    ) -> None:
        other_org_model = create_model("org-b", user=CUSTOMER_B1)
        version_id = insert_version(engine, other_org_model["id"])

        assert _change(client, version_id, status="TRAINING").status_code == 200

    def test_unknown_version_is_not_found(self, client: TestClient) -> None:
        response = _change(client, str(uuid.uuid4()), status="TRAINING")

        assert response.status_code == 404
        assert _error_code(response) == "MODEL_VERSION_NOT_FOUND"


class TestDeletedModel:
    def test_soft_deleted_model_version_can_change_and_survives_restore(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        version_id = insert_version(engine, model["id"], status=ModelVersionStatus.TRAINING)
        client.delete(f"{MODELS}/{model['id']}", headers=as_user(CUSTOMER_A1))

        response = _change(
            client,
            version_id,
            status="READY",
            artifact_uri=_artifact_uri(model["id"], version_id),
        )
        restored = client.post(f"{MODELS}/{model['id']}/restore", headers=as_user(CUSTOMER_A1))

        assert response.status_code == 200
        assert restored.json()["versions"][0]["status"] == "READY"

    def test_model_past_restore_deadline_is_treated_as_purged(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        version_id = insert_version(engine, model["id"], status=ModelVersionStatus.TRAINING)
        client.delete(f"{MODELS}/{model['id']}", headers=as_user(CUSTOMER_A1))
        with Session(engine) as session, session.begin():
            session.execute(
                update(MLModel)
                .where(MLModel.id == uuid.UUID(model["id"]))
                .values(
                    deleted_at=func.now() - timedelta(days=31),
                    purge_at=func.now() - timedelta(days=1),
                )
            )

        response = _change(client, version_id, status="FAILED")

        assert response.status_code == 404
        assert _error_code(response) == "MODEL_VERSION_NOT_FOUND"

    def test_purged_model_version_is_not_found(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        version_id = insert_version(engine, model["id"])
        with Session(engine) as session, session.begin():
            session.execute(delete(MLModel).where(MLModel.id == uuid.UUID(model["id"])))

        response = _change(client, version_id, status="TRAINING")

        assert response.status_code == 404
        assert _error_code(response) == "MODEL_VERSION_NOT_FOUND"


def test_concurrent_transitions_allow_only_one(
    app: FastAPI, engine: Engine, model: dict[str, Any]
) -> None:
    """같은 TRAINING 버전에 READY와 FAILED가 동시에 오면 하나만 반영된다."""
    version_id = insert_version(engine, model["id"], status=ModelVersionStatus.TRAINING)
    bodies = [
        {"status": "READY", "artifact_uri": _artifact_uri(model["id"], version_id)},
        {"status": "FAILED"},
    ] * 3

    def change(body: dict[str, Any]) -> int:
        return _change(TestClient(app), version_id, **body).status_code

    with ThreadPoolExecutor(max_workers=len(bodies)) as executor:
        statuses = sorted(executor.map(change, bodies))

    assert statuses == [200, 409, 409, 409, 409, 409]
    assert _row_version(engine, model["id"]) == 2


def test_status_change_is_logged(
    client: TestClient,
    engine: Engine,
    model: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    version_id = insert_version(engine, model["id"])

    with caplog.at_level("INFO", logger="app.services.model_version_service"):
        _change(client, version_id, status="TRAINING")

    record = next(r for r in caplog.records if r.message == "Model version status changed")
    assert record.version_id == version_id  # type: ignore[attr-defined]
    assert record.from_status == "PENDING"  # type: ignore[attr-defined]
    assert record.to_status == "TRAINING"  # type: ignore[attr-defined]
