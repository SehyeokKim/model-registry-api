"""모델 Core API (생성, 조회, 수정, 삭제) 통합 테스트."""

import unicodedata
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.entities import MLModel, MLModelVersion, ModelVersionStatus
from scripts.seed import seed_demo_data
from tests.support import (
    CUSTOMER_A1,
    CUSTOMER_A2,
    CUSTOMER_B1,
    ML_ENGINEER,
    ORGANIZATION_A,
    as_user,
)

MODELS = "/api/v1/models"


@pytest.fixture(autouse=True)
def _demo_data(engine: Engine) -> None:
    seed_demo_data(engine)


@pytest.fixture
def create_model(client: TestClient) -> Callable[..., dict[str, Any]]:
    def _create(
        name: str = "churn-predictor", user: uuid.UUID = CUSTOMER_A1, **fields: Any
    ) -> dict[str, Any]:
        body = {"name": name, "task_type": "CLASSIFICATION", **fields}
        response = client.post(MODELS, json=body, headers=as_user(user))
        assert response.status_code == 201, response.text
        return response.json()

    return _create


def _add_version(engine: Engine, model_id: str, version_no: int = 1) -> None:
    """버전 API는 이후 PR에서 추가되므로 DB에 직접 넣는다."""
    with Session(engine) as session, session.begin():
        session.add(
            MLModelVersion(
                model_id=uuid.UUID(model_id),
                version_no=version_no,
                created_by=CUSTOMER_A1,
                status=ModelVersionStatus.PENDING,
            )
        )


def _error_code(response: Any) -> str:
    return response.json()["error"]["code"]


class TestRequester:
    def test_unknown_user_is_unauthorized(self, client: TestClient) -> None:
        response = client.post(
            MODELS, json={"name": "m", "task_type": "CLASSIFICATION"}, headers=as_user(uuid.uuid4())
        )

        assert response.status_code == 401
        assert _error_code(response) == "UNAUTHORIZED"

    def test_internal_user_cannot_call_customer_api(self, client: TestClient) -> None:
        response = client.post(
            MODELS, json={"name": "m", "task_type": "CLASSIFICATION"}, headers=as_user(ML_ENGINEER)
        )

        assert response.status_code == 403
        assert _error_code(response) == "FORBIDDEN"


class TestCreate:
    def test_creates_model_in_requester_organization(self, client: TestClient) -> None:
        response = client.post(
            MODELS,
            json={"name": "churn-predictor", "task_type": "CLASSIFICATION", "description": "이탈"},
            headers=as_user(CUSTOMER_A1),
        )

        assert response.status_code == 201
        body = response.json()
        assert response.headers["Location"] == f"{MODELS}/{body['id']}"
        assert body["organization_id"] == str(ORGANIZATION_A)
        assert body["created_by"] == str(CUSTOMER_A1)
        assert body["name"] == "churn-predictor"
        assert body["description"] == "이탈"
        assert body["versions"] == []
        assert body["created_at"].endswith("Z")

    def test_name_is_normalized_before_saving(self, client: TestClient) -> None:
        name = unicodedata.normalize("NFD", "  수요예측  ")

        response = client.post(
            MODELS, json={"name": name, "task_type": "REGRESSION"}, headers=as_user(CUSTOMER_A1)
        )

        assert response.json()["name"] == unicodedata.normalize("NFC", "수요예측")

    def test_duplicate_active_name_in_organization_is_conflict(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        create_model("same")

        response = client.post(
            MODELS,
            json={"name": "same", "task_type": "CLASSIFICATION"},
            headers=as_user(CUSTOMER_A2),
        )

        assert response.status_code == 409
        assert _error_code(response) == "MODEL_NAME_CONFLICT"

    def test_nfd_variant_of_existing_name_is_conflict(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        create_model(unicodedata.normalize("NFC", "한국모델"))

        response = client.post(
            MODELS,
            json={"name": unicodedata.normalize("NFD", "한국모델"), "task_type": "CLASSIFICATION"},
            headers=as_user(CUSTOMER_A1),
        )

        assert response.status_code == 409

    def test_same_name_in_other_organization_is_allowed(
        self, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        create_model("same", user=CUSTOMER_A1)
        create_model("same", user=CUSTOMER_B1)

    def test_validation_error_lists_fields_without_echoing_input(self, client: TestClient) -> None:
        response = client.post(
            MODELS, json={"name": "", "task_type": "CLUSTERING"}, headers=as_user(CUSTOMER_A1)
        )

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert {tuple(d["location"]) for d in error["details"]} == {
            ("body", "name"),
            ("body", "task_type"),
        }
        assert all("input" not in d for d in error["details"])

    def test_malformed_json_is_validation_error(self, client: TestClient) -> None:
        response = client.post(
            MODELS,
            content=b"{not json",
            headers={**as_user(CUSTOMER_A1), "Content-Type": "application/json"},
        )

        assert response.status_code == 422
        assert _error_code(response) == "VALIDATION_ERROR"

    def test_concurrent_creates_with_same_name_allow_only_one(self, app: FastAPI) -> None:
        def create(_: int) -> int:
            client = TestClient(app)
            return client.post(
                MODELS,
                json={"name": "race", "task_type": "CLASSIFICATION"},
                headers=as_user(CUSTOMER_A1),
            ).status_code

        with ThreadPoolExecutor(max_workers=5) as executor:
            statuses = sorted(executor.map(create, range(5)))

        assert statuses == [201, 409, 409, 409, 409]


class TestGet:
    def test_returns_model_with_versions_newest_first(
        self, client: TestClient, engine: Engine, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model()
        _add_version(engine, model["id"], 1)
        _add_version(engine, model["id"], 2)

        response = client.get(f"{MODELS}/{model['id']}", headers=as_user(CUSTOMER_A1))

        assert response.status_code == 200
        assert [v["version_no"] for v in response.json()["versions"]] == [2, 1]

    def test_same_organization_user_can_read(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model(user=CUSTOMER_A1)

        response = client.get(f"{MODELS}/{model['id']}", headers=as_user(CUSTOMER_A2))

        assert response.status_code == 200

    def test_other_organization_gets_not_found(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model(user=CUSTOMER_A1)

        response = client.get(f"{MODELS}/{model['id']}", headers=as_user(CUSTOMER_B1))

        assert response.status_code == 404
        assert _error_code(response) == "MODEL_NOT_FOUND"

    def test_unknown_model_is_not_found(self, client: TestClient) -> None:
        response = client.get(f"{MODELS}/{uuid.uuid4()}", headers=as_user(CUSTOMER_A1))

        assert response.status_code == 404

    def test_non_uuid_model_id_is_validation_error(self, client: TestClient) -> None:
        response = client.get(f"{MODELS}/abc", headers=as_user(CUSTOMER_A1))

        assert response.status_code == 422
        assert response.json()["error"]["details"][0]["location"] == ["path", "model_id"]


class TestUpdate:
    def test_omitted_fields_are_kept_and_null_description_clears(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model(description="old")

        response = client.patch(
            f"{MODELS}/{model['id']}",
            json={"name": "renamed", "description": None},
            headers=as_user(CUSTOMER_A1),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "renamed"
        assert body["description"] is None
        assert body["task_type"] == model["task_type"]
        assert body["updated_at"] > model["updated_at"]

    def test_same_organization_user_can_update(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model(user=CUSTOMER_A1)

        response = client.patch(
            f"{MODELS}/{model['id']}", json={"description": "x"}, headers=as_user(CUSTOMER_A2)
        )

        assert response.status_code == 200

    def test_empty_update_is_rejected(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model()

        response = client.patch(f"{MODELS}/{model['id']}", json={}, headers=as_user(CUSTOMER_A1))

        assert response.status_code == 422
        assert _error_code(response) == "EMPTY_UPDATE"

    @pytest.mark.parametrize("body", [{"name": None}, {"name": "  "}, {"task_type": None}])
    def test_required_fields_cannot_be_cleared(
        self,
        client: TestClient,
        create_model: Callable[..., dict[str, Any]],
        body: dict[str, Any],
    ) -> None:
        model = create_model()

        response = client.patch(f"{MODELS}/{model['id']}", json=body, headers=as_user(CUSTOMER_A1))

        assert response.status_code == 422
        assert _error_code(response) == "VALIDATION_ERROR"

    def test_task_type_can_change_while_model_has_no_versions(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model()

        response = client.patch(
            f"{MODELS}/{model['id']}",
            json={"task_type": "REGRESSION"},
            headers=as_user(CUSTOMER_A1),
        )

        assert response.json()["task_type"] == "REGRESSION"

    def test_task_type_is_locked_once_a_version_exists(
        self, client: TestClient, engine: Engine, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model()
        _add_version(engine, model["id"])

        response = client.patch(
            f"{MODELS}/{model['id']}",
            json={"task_type": "REGRESSION"},
            headers=as_user(CUSTOMER_A1),
        )

        assert response.status_code == 409
        assert _error_code(response) == "TASK_TYPE_LOCKED"

    def test_sending_current_task_type_is_allowed_with_versions(
        self, client: TestClient, engine: Engine, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model()
        _add_version(engine, model["id"])

        response = client.patch(
            f"{MODELS}/{model['id']}",
            json={"task_type": "CLASSIFICATION", "description": "x"},
            headers=as_user(CUSTOMER_A1),
        )

        assert response.status_code == 200

    def test_rename_conflict_rolls_back_the_whole_update(
        self,
        client: TestClient,
        engine: Engine,
        create_model: Callable[..., dict[str, Any]],
    ) -> None:
        create_model("taken")
        model = create_model("mine", description="original")

        response = client.patch(
            f"{MODELS}/{model['id']}",
            json={"name": "taken", "description": "changed"},
            headers=as_user(CUSTOMER_A1),
        )

        assert response.status_code == 409
        with Session(engine) as session:
            saved = session.get_one(MLModel, uuid.UUID(model["id"]))
        assert (saved.name, saved.description, saved.row_version) == ("mine", "original", 1)

    def test_other_organization_cannot_update(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model(user=CUSTOMER_A1)

        response = client.patch(
            f"{MODELS}/{model['id']}", json={"description": "x"}, headers=as_user(CUSTOMER_B1)
        )

        assert response.status_code == 404

    def test_each_write_increments_row_version(
        self, client: TestClient, engine: Engine, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model()
        for description in ("a", "b"):
            client.patch(
                f"{MODELS}/{model['id']}",
                json={"description": description},
                headers=as_user(CUSTOMER_A1),
            )

        with Session(engine) as session:
            assert session.get_one(MLModel, uuid.UUID(model["id"])).row_version == 3


class TestDelete:
    def test_soft_deletes_with_restore_deadline(
        self, client: TestClient, engine: Engine, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model()

        response = client.delete(f"{MODELS}/{model['id']}", headers=as_user(CUSTOMER_A1))

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == model["id"]
        deleted_at = datetime.fromisoformat(body["deleted_at"])
        purge_at = datetime.fromisoformat(body["purge_at"])
        assert purge_at - deleted_at == timedelta(days=30)
        with Session(engine) as session:
            assert session.scalar(select(MLModel.id).where(MLModel.id == uuid.UUID(model["id"])))

    def test_deleted_model_is_excluded_from_read_update_and_delete(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model()
        path = f"{MODELS}/{model['id']}"
        client.delete(path, headers=as_user(CUSTOMER_A1))

        assert client.get(path, headers=as_user(CUSTOMER_A1)).status_code == 404
        assert (
            client.patch(path, json={"description": "x"}, headers=as_user(CUSTOMER_A1)).status_code
            == 404
        )
        assert client.delete(path, headers=as_user(CUSTOMER_A1)).status_code == 404

    def test_name_can_be_reused_after_delete(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model("reusable")
        client.delete(f"{MODELS}/{model['id']}", headers=as_user(CUSTOMER_A1))

        create_model("reusable")

    def test_other_organization_cannot_delete(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model(user=CUSTOMER_A1)

        response = client.delete(f"{MODELS}/{model['id']}", headers=as_user(CUSTOMER_B1))

        assert response.status_code == 404
        assert (
            client.get(f"{MODELS}/{model['id']}", headers=as_user(CUSTOMER_A1)).status_code == 200
        )


def test_request_log_includes_organization_and_user(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    from app.core.logging import RequestContextFilter

    caplog.handler.addFilter(RequestContextFilter())
    with caplog.at_level("INFO", logger="app.access"):
        client.get(f"{MODELS}/{uuid.uuid4()}", headers=as_user(CUSTOMER_A1))

    record = next(r for r in caplog.records if r.name == "app.access")
    assert record.organization_id == str(ORGANIZATION_A)  # type: ignore[attr-defined]
    assert record.user_id == str(CUSTOMER_A1)  # type: ignore[attr-defined]
