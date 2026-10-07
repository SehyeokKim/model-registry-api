"""모델 ETag와 If-Match 조건부 쓰기 통합 테스트 (ADR 0007)."""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.db.entities import MLModel, ModelVersionStatus
from scripts.seed import seed_demo_data
from tests.support import CUSTOMER_A1, CUSTOMER_A2, ML_ENGINEER, as_user, insert_version

MODELS = "/api/v1/models"


@pytest.fixture(autouse=True)
def _demo_data(engine: Engine) -> None:
    seed_demo_data(engine)


@pytest.fixture
def model(create_model: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    return create_model()


def _path(model: dict[str, Any]) -> str:
    return f"{MODELS}/{model['id']}"


def _headers(user: uuid.UUID = CUSTOMER_A1, if_match: str | None = None) -> dict[str, str]:
    headers = as_user(user)
    if if_match is not None:
        headers["If-Match"] = if_match
    return headers


def _etag(client: TestClient, model: dict[str, Any]) -> str:
    return client.get(_path(model), headers=_headers()).headers["ETag"]


def _model_row(engine: Engine, model: dict[str, Any]) -> MLModel:
    with Session(engine) as session:
        return session.get_one(MLModel, uuid.UUID(model["id"]))


def _error_code(response: Any) -> str:
    return response.json()["error"]["code"]


class TestEtagHeader:
    def test_single_model_responses_carry_etag(
        self, client: TestClient, create_model: Callable[..., dict[str, Any]]
    ) -> None:
        created = client.post(
            MODELS, json={"name": "m", "task_type": "REGRESSION"}, headers=_headers()
        )
        path = f"{MODELS}/{created.json()['id']}"

        fetched = client.get(path, headers=_headers())
        updated = client.patch(path, json={"description": "d"}, headers=_headers())
        client.delete(path, headers=_headers())
        restored = client.post(f"{path}/restore", headers=_headers())

        assert created.headers["ETag"] == '"1"'
        assert fetched.headers["ETag"] == '"1"'
        assert updated.headers["ETag"] == '"2"'
        assert restored.headers["ETag"] == '"4"'

    def test_every_write_changes_etag(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        """ADR 0007: 모델 단건 응답이 바뀌는 모든 쓰기에서 ETag가 바뀐다."""
        path = _path(model)
        seen = [_etag(client, model)]

        client.patch(path, json={"description": "d"}, headers=_headers())
        seen.append(_etag(client, model))
        version = client.post(f"{path}/versions", json={}, headers=_headers()).json()
        seen.append(_etag(client, model))
        client.patch(
            f"/internal/v1/model-versions/{version['id']}/status",
            json={"status": "TRAINING"},
            headers=as_user(ML_ENGINEER),
        )
        seen.append(_etag(client, model))
        client.delete(path, headers=_headers())
        client.post(f"{path}/restore", headers=_headers())
        seen.append(_etag(client, model))

        assert seen == ['"1"', '"2"', '"3"', '"4"', '"6"']

    def test_failed_write_keeps_etag(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        insert_version(engine, model["id"], status=ModelVersionStatus.READY)

        client.patch(_path(model), json={"task_type": "REGRESSION"}, headers=_headers())

        assert _etag(client, model) == '"1"'


class TestIfMatch:
    def test_matching_etag_allows_update(self, client: TestClient, model: dict[str, Any]) -> None:
        response = client.patch(
            _path(model), json={"description": "d"}, headers=_headers(if_match='"1"')
        )

        assert response.status_code == 200
        assert response.headers["ETag"] == '"2"'

    def test_stale_etag_is_rejected_with_current_etag(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        client.patch(_path(model), json={"description": "first"}, headers=_headers())

        response = client.patch(
            _path(model), json={"description": "second"}, headers=_headers(if_match='"1"')
        )

        assert response.status_code == 412
        assert _error_code(response) == "PRECONDITION_FAILED"
        assert response.headers["ETag"] == '"2"'
        assert _model_row(engine, model).description == "first"

    def test_lost_update_between_two_users_is_prevented(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        """ADR 0007 예: A와 B가 같은 화면을 열고, A가 먼저 저장하면 B의 저장은 거부된다."""
        etag_seen_by_a = _etag(client, model)
        etag_seen_by_b = _etag(client, model)

        saved_by_a = client.patch(
            _path(model),
            json={"description": "A의 설명"},
            headers=_headers(CUSTOMER_A1, if_match=etag_seen_by_a),
        )
        saved_by_b = client.patch(
            _path(model),
            json={"name": "renamed-by-b", "description": None},
            headers=_headers(CUSTOMER_A2, if_match=etag_seen_by_b),
        )

        assert saved_by_a.status_code == 200
        assert saved_by_b.status_code == 412
        row = _model_row(engine, model)
        assert row.description == "A의 설명"
        assert row.name == model["name"]

    def test_version_created_meanwhile_is_detected(
        self, client: TestClient, model: dict[str, Any]
    ) -> None:
        """ADR 0007 예: 버전이 생긴 사실도 모델이 바뀐 것으로 감지한다."""
        etag = _etag(client, model)
        client.post(f"{_path(model)}/versions", json={}, headers=_headers())

        response = client.patch(
            _path(model), json={"task_type": "REGRESSION"}, headers=_headers(if_match=etag)
        )

        assert response.status_code == 412

    def test_without_if_match_last_write_wins(
        self, client: TestClient, model: dict[str, Any]
    ) -> None:
        client.patch(_path(model), json={"description": "first"}, headers=_headers())

        response = client.patch(_path(model), json={"description": "second"}, headers=_headers())

        assert response.status_code == 200

    @pytest.mark.parametrize("if_match", ["*", '"9", "1"'])
    def test_wildcard_or_list_containing_current_etag_passes(
        self, client: TestClient, model: dict[str, Any], if_match: str
    ) -> None:
        response = client.patch(
            _path(model), json={"description": "d"}, headers=_headers(if_match=if_match)
        )

        assert response.status_code == 200

    def test_weak_etag_does_not_match(self, client: TestClient, model: dict[str, Any]) -> None:
        response = client.patch(
            _path(model), json={"description": "d"}, headers=_headers(if_match='W/"1"')
        )

        assert response.status_code == 412

    @pytest.mark.parametrize("if_match", ["1", '"1', "W/1"])
    def test_malformed_if_match_is_validation_error(
        self, client: TestClient, engine: Engine, model: dict[str, Any], if_match: str
    ) -> None:
        response = client.patch(
            _path(model), json={"description": "d"}, headers=_headers(if_match=if_match)
        )

        assert response.status_code == 422
        assert _error_code(response) == "VALIDATION_ERROR"
        assert response.json()["error"]["details"][0]["location"] == ["header", "if-match"]
        assert _model_row(engine, model).row_version == 1

    def test_stale_etag_rejects_delete(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        client.patch(_path(model), json={"description": "d"}, headers=_headers())

        response = client.delete(_path(model), headers=_headers(if_match='"1"'))

        assert response.status_code == 412
        assert response.headers["ETag"] == '"2"'
        assert _model_row(engine, model).deleted_at is None

    def test_delete_response_etag_can_be_used_to_restore(
        self, client: TestClient, model: dict[str, Any]
    ) -> None:
        """삭제된 모델은 조회할 수 없으므로, 복원의 If-Match는 삭제 응답의 ETag로 얻는다."""
        deleted = client.delete(_path(model), headers=_headers())

        restored = client.post(
            f"{_path(model)}/restore", headers=_headers(if_match=deleted.headers["ETag"])
        )

        assert deleted.headers["ETag"] == '"2"'
        assert restored.status_code == 200

    def test_stale_etag_rejects_restore(
        self, client: TestClient, engine: Engine, model: dict[str, Any]
    ) -> None:
        client.delete(_path(model), headers=_headers())

        stale = client.post(f"{_path(model)}/restore", headers=_headers(if_match='"1"'))
        current = client.post(f"{_path(model)}/restore", headers=_headers(if_match='"2"'))

        assert stale.status_code == 412
        assert current.status_code == 200


class TestEvaluationOrder:
    """RFC 9110 §13.2.1: 조건 없이도 실패할 요청은 조건을 무시하고 그 오류를 낸다."""

    def test_unknown_model_is_not_found_even_with_stale_etag(self, client: TestClient) -> None:
        response = client.patch(
            f"{MODELS}/{uuid.uuid4()}", json={"description": "d"}, headers=_headers(if_match='"9"')
        )

        assert response.status_code == 404

    def test_restoring_active_model_is_conflict_even_with_stale_etag(
        self, client: TestClient, model: dict[str, Any]
    ) -> None:
        response = client.post(f"{_path(model)}/restore", headers=_headers(if_match='"9"'))

        assert response.status_code == 409
        assert _error_code(response) == "MODEL_NOT_DELETED"
