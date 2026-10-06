"""모델 버전 API 통합 테스트."""

import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, select
from sqlalchemy.orm import Session, undefer

from app.db.entities import MLModel, MLModelVersion, ModelVersionStatus
from scripts.seed import seed_demo_data
from tests.support import (
    CUSTOMER_A1,
    CUSTOMER_A2,
    CUSTOMER_B1,
    ML_ENGINEER,
    as_user,
    insert_version,
)

MODELS = "/api/v1/models"

INPUT_SCHEMA = {
    "type": "object",
    "properties": {"tenure_months": {"type": "integer", "minimum": 0}},
    "required": ["tenure_months"],
}
OUTPUT_SCHEMA = {"type": "object", "properties": {"churn": {"type": "boolean"}}}


@pytest.fixture(autouse=True)
def _demo_data(engine: Engine) -> None:
    seed_demo_data(engine)


@pytest.fixture
def model(create_model: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    return create_model()


def _versions_path(model_id: str) -> str:
    return f"{MODELS}/{model_id}/versions"


def _create_version(
    client: TestClient, model_id: str, user: uuid.UUID = CUSTOMER_A1, **body: Any
) -> Any:
    return client.post(_versions_path(model_id), json=body, headers=as_user(user))


def _error_code(response: Any) -> str:
    return response.json()["error"]["code"]


def _version_count(engine: Engine, model_id: str) -> int:
    with Session(engine) as session:
        return (
            session.scalar(
                select(func.count()).where(MLModelVersion.model_id == uuid.UUID(model_id))
            )
            or 0
        )


class TestCreate:
    def test_creates_pending_version_with_schemas(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        response = _create_version(
            client,
            model["id"],
            framework="xgboost",
            input_schema=INPUT_SCHEMA,
            output_schema=OUTPUT_SCHEMA,
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["model_id"] == model["id"]
        assert body["version_no"] == 1
        assert body["status"] == "PENDING"
        assert body["parent_version_id"] is None
        assert body["artifact_uri"] is None
        assert body["framework"] == "xgboost"
        assert body["input_schema"] == INPUT_SCHEMA
        assert body["output_schema"] == OUTPUT_SCHEMA
        assert body["created_by"] == str(CUSTOMER_A1)
        assert body["created_at"].endswith("Z")
        with Session(engine) as session:
            stored = session.get_one(
                MLModelVersion,
                uuid.UUID(body["id"]),
                options=[undefer(MLModelVersion.input_schema)],
            )
            assert stored.input_schema == INPUT_SCHEMA

    def test_version_numbers_increase_per_model(
        self,
        client: TestClient,
        model: dict[str, Any],
        create_model: Callable[..., dict[str, Any]],
    ) -> None:
        other = create_model("other")

        numbers = [_create_version(client, model["id"]).json()["version_no"] for _ in range(3)]

        assert numbers == [1, 2, 3]
        assert _create_version(client, other["id"]).json()["version_no"] == 1

    def test_new_version_appears_in_model_detail(
        self, client: TestClient, model: dict[str, Any]
    ) -> None:
        _create_version(client, model["id"], input_schema=INPUT_SCHEMA)
        _create_version(client, model["id"])

        detail = client.get(f"{MODELS}/{model['id']}", headers=as_user(CUSTOMER_A1)).json()

        assert [v["version_no"] for v in detail["versions"]] == [2, 1]
        # 모델 조회의 버전 목록은 요약만 담는다. 스키마는 버전 생성 응답에서 확인한다.
        assert "input_schema" not in detail["versions"][0]

    def test_increments_model_row_version(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        _create_version(client, model["id"])

        with Session(engine) as session:
            assert session.get_one(MLModel, uuid.UUID(model["id"])).row_version == 2

    def test_allowed_while_previous_version_is_training(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        insert_version(engine, model["id"], 1, ModelVersionStatus.TRAINING)

        response = _create_version(client, model["id"])

        assert response.status_code == 201
        assert response.json()["version_no"] == 2

    def test_same_organization_user_can_create(
        self, client: TestClient, model: dict[str, Any]
    ) -> None:
        response = _create_version(client, model["id"], user=CUSTOMER_A2)

        assert response.status_code == 201
        assert response.json()["created_by"] == str(CUSTOMER_A2)

    def test_locks_task_type_of_model(self, client: TestClient, model: dict[str, Any]) -> None:
        _create_version(client, model["id"])

        response = client.patch(
            f"{MODELS}/{model['id']}",
            json={"task_type": "REGRESSION"},
            headers=as_user(CUSTOMER_A1),
        )

        assert response.status_code == 409
        assert _error_code(response) == "TASK_TYPE_LOCKED"


class TestParentVersion:
    def test_ready_version_of_same_model_can_be_parent(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        parent_id = insert_version(engine, model["id"], 1, ModelVersionStatus.READY)

        response = _create_version(client, model["id"], parent_version_id=parent_id)

        assert response.status_code == 201
        assert response.json()["parent_version_id"] == parent_id

    @pytest.mark.parametrize(
        "status",
        [
            ModelVersionStatus.PENDING,
            ModelVersionStatus.TRAINING,
            ModelVersionStatus.FAILED,
            ModelVersionStatus.ARCHIVED,
        ],
    )
    def test_version_not_ready_cannot_be_parent(
        self,
        client: TestClient,
        engine: Engine,
        model: dict[str, Any],
        status: ModelVersionStatus,
    ) -> None:
        parent_id = insert_version(engine, model["id"], 1, status)

        response = _create_version(client, model["id"], parent_version_id=parent_id)

        assert response.status_code == 422
        assert _error_code(response) == "INVALID_PARENT_VERSION"
        assert _version_count(engine, model["id"]) == 1

    def test_version_of_other_model_cannot_be_parent(
        self,
        client: TestClient,
        engine: Engine,
        model: dict[str, Any],
        create_model: Callable[..., dict[str, Any]],
    ) -> None:
        other = create_model("other")
        parent_id = insert_version(engine, other["id"], 1, ModelVersionStatus.READY)

        response = _create_version(client, model["id"], parent_version_id=parent_id)

        assert response.status_code == 422
        assert _error_code(response) == "INVALID_PARENT_VERSION"

    def test_unknown_parent_is_invalid(self, client: TestClient, model: dict[str, Any]) -> None:
        response = _create_version(client, model["id"], parent_version_id=str(uuid.uuid4()))

        assert response.status_code == 422
        assert _error_code(response) == "INVALID_PARENT_VERSION"


class TestValidation:
    def test_invalid_json_schema_is_rejected_with_location(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        response = _create_version(
            client, model["id"], output_schema={"properties": {"churn": {"type": "bool"}}}
        )

        assert response.status_code == 422
        assert _error_code(response) == "VALIDATION_ERROR"
        (detail,) = response.json()["error"]["details"]
        assert detail["location"] == ["body", "output_schema"]
        assert "/properties/churn/type" in detail["message"]
        assert _version_count(engine, model["id"]) == 0

    def test_undefined_field_is_rejected(self, client: TestClient, model: dict[str, Any]) -> None:
        response = _create_version(client, model["id"], status="READY")

        assert response.status_code == 422
        assert _error_code(response) == "VALIDATION_ERROR"


class TestAccess:
    def test_deleted_model_is_not_found(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        client.delete(f"{MODELS}/{model['id']}", headers=as_user(CUSTOMER_A1))

        response = _create_version(client, model["id"])

        assert response.status_code == 404
        assert _error_code(response) == "MODEL_NOT_FOUND"
        assert _version_count(engine, model["id"]) == 0

    def test_other_organization_gets_not_found(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        response = _create_version(client, model["id"], user=CUSTOMER_B1)

        assert response.status_code == 404
        assert _error_code(response) == "MODEL_NOT_FOUND"
        assert _version_count(engine, model["id"]) == 0

    def test_unknown_model_is_not_found(self, client: TestClient) -> None:
        response = _create_version(client, str(uuid.uuid4()))

        assert response.status_code == 404
        assert _error_code(response) == "MODEL_NOT_FOUND"

    def test_internal_user_cannot_call_customer_api(
        self, client: TestClient, model: dict[str, Any]
    ) -> None:
        response = _create_version(client, model["id"], user=ML_ENGINEER)

        assert response.status_code == 403
        assert _error_code(response) == "FORBIDDEN"


def test_concurrent_creates_get_sequential_numbers(
    app: FastAPI, engine: Engine, model: dict[str, Any]
) -> None:
    """ADR 0003: 같은 모델에 동시에 생성해도 번호는 정확히 1..N이다."""
    requests = 10

    def create(_: int) -> int:
        response = _create_version(TestClient(app), model["id"])
        assert response.status_code == 201, response.text
        return response.json()["version_no"]

    with ThreadPoolExecutor(max_workers=requests) as executor:
        numbers = sorted(executor.map(create, range(requests)))

    assert numbers == list(range(1, requests + 1))
    with Session(engine) as session:
        assert session.get_one(MLModel, uuid.UUID(model["id"])).row_version == 1 + requests


def test_model_detail_does_not_read_version_schemas(
    app: FastAPI, client: TestClient, model: dict[str, Any]
) -> None:
    """스키마는 클 수 있고 모델 조회 응답에 쓰지 않으므로 DB에서 읽지 않는다."""
    _create_version(client, model["id"], input_schema=INPUT_SCHEMA, output_schema=OUTPUT_SCHEMA)
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        statements.append(statement)

    event.listen(app.state.engine, "before_cursor_execute", record)
    try:
        response = client.get(f"{MODELS}/{model['id']}", headers=as_user(CUSTOMER_A1))
    finally:
        event.remove(app.state.engine, "before_cursor_execute", record)

    assert response.status_code == 200
    version_queries = [s for s in statements if "FROM model_versions" in s]
    assert version_queries
    assert not any("input_schema" in s or "output_schema" in s for s in version_queries)
