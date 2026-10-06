"""모델 API (생성, 조회, 수정, 삭제, 목록, 복원) 통합 테스트."""

import unicodedata
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, event, func, select, update
from sqlalchemy.orm import Session

from app.db.entities import MLModel
from scripts.seed import seed_demo_data
from tests.support import (
    CUSTOMER_A1,
    CUSTOMER_A2,
    CUSTOMER_B1,
    ML_ENGINEER,
    ORGANIZATION_A,
    as_user,
    insert_version,
)

MODELS = "/api/v1/models"


@pytest.fixture(autouse=True)
def _demo_data(engine: Engine) -> None:
    seed_demo_data(engine)


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
        insert_version(engine, model["id"], 1)
        insert_version(engine, model["id"], 2)

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
        insert_version(engine, model["id"])

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
        insert_version(engine, model["id"])

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


def _set_timestamps(engine: Engine, model_id: str, **values: Any) -> None:
    """생성 시각, 삭제 시각처럼 서버가 정하는 값을 테스트 상황에 맞게 바꾼다."""
    with Session(engine) as session, session.begin():
        session.execute(update(MLModel).where(MLModel.id == uuid.UUID(model_id)).values(**values))


def _expire_restore_window(engine: Engine, model_id: str) -> None:
    """복원 기한이 지났지만 아직 영구 삭제되지 않은 상태로 만든다."""
    _set_timestamps(
        engine,
        model_id,
        deleted_at=func.now() - timedelta(days=31),
        purge_at=func.now() - timedelta(days=1),
    )


def _list(client: TestClient, user: uuid.UUID = CUSTOMER_A1, **params: Any) -> Any:
    response = client.get(MODELS, params=params, headers=as_user(user))
    assert response.status_code == 200, response.text
    return response.json()


def _names(body: dict[str, Any]) -> list[str]:
    return [item["name"] for item in body["items"]]


class TestList:
    def test_lists_active_models_newest_first_by_default(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        create_model("first")
        create_model("second")
        deleted = create_model("deleted")
        client.delete(f"{MODELS}/{deleted['id']}", headers=as_user(CUSTOMER_A1))

        body = _list(client)

        assert _names(body) == ["second", "first"]
        assert body["next_cursor"] is None

    def test_item_includes_version_summary_and_delete_fields(
        self, client: TestClient, engine: Engine, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        with_versions = create_model("with-versions", description="설명")
        insert_version(engine, with_versions["id"], 1)
        insert_version(engine, with_versions["id"], 2)
        create_model("no-versions")

        items = {item["name"]: item for item in _list(client)["items"]}

        assert items["with-versions"]["version_count"] == 2
        assert items["with-versions"]["latest_version_no"] == 2
        assert items["with-versions"]["description"] == "설명"
        assert items["no-versions"]["version_count"] == 0
        assert items["no-versions"]["latest_version_no"] is None
        assert items["no-versions"]["deleted_at"] is None
        assert items["no-versions"]["purge_at"] is None
        assert items["no-versions"]["created_at"].endswith("Z")
        assert "versions" not in items["no-versions"]

    def test_deleted_filter_shows_only_restorable_models(
        self, client: TestClient, engine: Engine, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        create_model("active")
        restorable = create_model("restorable")
        expired = create_model("expired")
        for model in (restorable, expired):
            client.delete(f"{MODELS}/{model['id']}", headers=as_user(CUSTOMER_A1))
        _expire_restore_window(engine, expired["id"])

        deleted = _list(client, status="deleted")

        assert _names(deleted) == ["restorable"]
        assert deleted["items"][0]["purge_at"] is not None
        # 복원 기한이 지난 모델은 삭제가 확정된 것이므로 전체 범위에도 넣지 않는다.
        assert _names(_list(client, status="all")) == ["restorable", "active"]

    def test_undefined_status_is_validation_error(self, client: TestClient) -> None:
        response = client.get(MODELS, params={"status": "purged"}, headers=as_user(CUSTOMER_A1))

        assert response.status_code == 422
        assert _error_code(response) == "VALIDATION_ERROR"

    def test_lists_only_requester_organization(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        create_model("org-a", user=CUSTOMER_A1)
        create_model("org-b", user=CUSTOMER_B1)

        assert _names(_list(client, user=CUSTOMER_A2)) == ["org-a"]
        assert _names(_list(client, user=CUSTOMER_B1)) == ["org-b"]

    def test_internal_user_cannot_list(self, client: TestClient) -> None:
        response = client.get(MODELS, headers=as_user(ML_ENGINEER))

        assert response.status_code == 403
        assert _error_code(response) == "FORBIDDEN"

    def test_cursor_walks_all_pages_without_gaps_or_duplicates(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        created = [create_model(f"model-{i}")["name"] for i in range(5)]

        seen: list[str] = []
        pages = 0
        cursor = None
        while True:
            params: dict[str, Any] = {"limit": 2}
            if cursor:
                params["cursor"] = cursor
            body = _list(client, **params)
            seen += _names(body)
            pages += 1
            cursor = body["next_cursor"]
            if cursor is None:
                break

        assert seen == list(reversed(created))
        assert pages == 3

    def test_last_full_page_has_no_next_cursor(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        create_model("a")
        create_model("b")

        assert _list(client, limit=2)["next_cursor"] is None

    def test_same_created_at_is_ordered_by_id(
        self, client: TestClient, engine: Engine, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        models = [create_model(f"tie-{i}") for i in range(3)]
        same_time = datetime(2026, 10, 6, 3, 0, tzinfo=UTC)
        for model in models:
            _set_timestamps(engine, model["id"], created_at=same_time)

        first = _list(client, limit=2)
        second = _list(client, limit=2, cursor=first["next_cursor"])

        ids = [item["id"] for item in first["items"] + second["items"]]
        assert ids == sorted((m["id"] for m in models), reverse=True)
        assert second["next_cursor"] is None

    @pytest.mark.parametrize("limit", [0, 101, "many"])
    def test_out_of_range_limit_is_validation_error(self, client: TestClient, limit: Any) -> None:
        response = client.get(MODELS, params={"limit": limit}, headers=as_user(CUSTOMER_A1))

        assert response.status_code == 422
        assert _error_code(response) == "VALIDATION_ERROR"

    def test_malformed_cursor_is_validation_error(self, client: TestClient) -> None:
        response = client.get(MODELS, params={"cursor": "broken"}, headers=as_user(CUSTOMER_A1))

        assert response.status_code == 422
        assert _error_code(response) == "VALIDATION_ERROR"
        assert response.json()["error"]["details"][0]["location"] == ["query", "cursor"]

    def test_query_count_does_not_grow_with_models(
        self,
        app: FastAPI,
        client: TestClient,
        engine: Engine,
        create_model: Callable[..., dict[str, Any]],
    ) -> None:
        statements: list[str] = []

        def record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
            statements.append(statement)

        def queries_for_list() -> int:
            statements.clear()
            event.listen(app.state.engine, "before_cursor_execute", record)
            try:
                _list(client)
            finally:
                event.remove(app.state.engine, "before_cursor_execute", record)
            return len(statements)

        model = create_model("one")
        insert_version(engine, model["id"])
        with_one = queries_for_list()
        for i in range(5):
            model = create_model(f"more-{i}")
            insert_version(engine, model["id"])

        assert queries_for_list() == with_one


class TestRestore:
    @staticmethod
    def _delete(client: TestClient, model_id: str) -> None:
        response = client.delete(f"{MODELS}/{model_id}", headers=as_user(CUSTOMER_A1))
        assert response.status_code == 200

    def test_restores_deleted_model_with_versions(
        self, client: TestClient, engine: Engine, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model("restorable")
        insert_version(engine, model["id"])
        self._delete(client, model["id"])

        response = client.post(f"{MODELS}/{model['id']}/restore", headers=as_user(CUSTOMER_A2))

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == model["id"]
        assert [v["version_no"] for v in body["versions"]] == [1]
        assert _names(_list(client)) == ["restorable"]
        assert _list(client, status="deleted")["items"] == []
        with Session(engine) as session:
            restored = session.get_one(MLModel, uuid.UUID(model["id"]))
            assert restored.deleted_at is None
            assert restored.purge_at is None
            # 생성 1 → 삭제 2 → 복원 3
            assert restored.row_version == 3

    def test_active_model_cannot_be_restored(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model()

        response = client.post(f"{MODELS}/{model['id']}/restore", headers=as_user(CUSTOMER_A1))

        assert response.status_code == 409
        assert _error_code(response) == "MODEL_NOT_DELETED"

    def test_model_past_restore_deadline_is_gone(
        self, client: TestClient, engine: Engine, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model()
        self._delete(client, model["id"])
        _expire_restore_window(engine, model["id"])

        response = client.post(f"{MODELS}/{model['id']}/restore", headers=as_user(CUSTOMER_A1))

        assert response.status_code == 410
        assert _error_code(response) == "MODEL_PURGED"

    def test_permanently_deleted_model_is_not_found(
        self, client: TestClient, engine: Engine, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model()
        self._delete(client, model["id"])
        with Session(engine) as session, session.begin():
            session.execute(delete(MLModel).where(MLModel.id == uuid.UUID(model["id"])))

        response = client.post(f"{MODELS}/{model['id']}/restore", headers=as_user(CUSTOMER_A1))

        assert response.status_code == 404
        assert _error_code(response) == "MODEL_NOT_FOUND"

    def test_other_organization_cannot_restore(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model = create_model()
        self._delete(client, model["id"])

        response = client.post(f"{MODELS}/{model['id']}/restore", headers=as_user(CUSTOMER_B1))

        assert response.status_code == 404
        assert _error_code(response) == "MODEL_NOT_FOUND"
        assert _names(_list(client, status="deleted")) == ["churn-predictor"]

    def test_unknown_model_is_not_found(self, client: TestClient) -> None:
        response = client.post(f"{MODELS}/{uuid.uuid4()}/restore", headers=as_user(CUSTOMER_A1))

        assert response.status_code == 404
        assert _error_code(response) == "MODEL_NOT_FOUND"

    def test_name_taken_by_active_model_is_conflict(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        old = create_model("same")
        self._delete(client, old["id"])
        create_model("same")

        response = client.post(f"{MODELS}/{old['id']}/restore", headers=as_user(CUSTOMER_A1))

        assert response.status_code == 409
        assert _error_code(response) == "MODEL_NAME_CONFLICT"
        assert _names(_list(client, status="deleted")) == ["same"]

    def test_concurrent_restores_of_same_name_allow_only_one(
        self, app: FastAPI, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        model_ids = []
        for _ in range(3):
            model = create_model("race")
            self._delete(client, model["id"])
            model_ids.append(model["id"])

        def restore(model_id: str) -> int:
            return (
                TestClient(app)
                .post(f"{MODELS}/{model_id}/restore", headers=as_user(CUSTOMER_A1))
                .status_code
            )

        with ThreadPoolExecutor(max_workers=3) as executor:
            statuses = sorted(executor.map(restore, model_ids))

        assert statuses == [200, 409, 409]
        assert _names(_list(client)) == ["race"]
