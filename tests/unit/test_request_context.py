import logging
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.logging import RequestContextFilter
from app.core.request_context import REQUEST_ID_HEADER, current_context, resolve_request_id


@pytest.mark.parametrize("value", ["abc-123_DEF", "a" * 128, str(uuid.uuid4())])
def test_resolve_request_id_keeps_valid_value(value: str) -> None:
    assert resolve_request_id(value) == value


@pytest.mark.parametrize(
    "value",
    [None, "", "a" * 129, "has space", "line\nbreak", "한글", "id;drop"],
)
def test_resolve_request_id_replaces_missing_or_invalid_value(value: str | None) -> None:
    resolved = resolve_request_id(value)

    assert resolved != value
    uuid.UUID(resolved)


def test_response_echoes_valid_request_id(client: TestClient) -> None:
    response = client.get("/health/live", headers={REQUEST_ID_HEADER: "req-123"})

    assert response.headers[REQUEST_ID_HEADER] == "req-123"


def test_response_has_generated_request_id_when_missing(client: TestClient) -> None:
    response = client.get("/health/live")

    uuid.UUID(response.headers[REQUEST_ID_HEADER])


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health/live", headers={REQUEST_ID_HEADER: "bad id"})

    assert response.headers[REQUEST_ID_HEADER] != "bad id"
    uuid.UUID(response.headers[REQUEST_ID_HEADER])


def test_not_found_response_also_has_request_id(client: TestClient) -> None:
    response = client.get("/does-not-exist", headers={REQUEST_ID_HEADER: "req-404"})

    assert response.status_code == 404
    assert response.headers[REQUEST_ID_HEADER] == "req-404"


def test_request_context_is_visible_inside_sync_endpoint(app: FastAPI) -> None:
    @app.get("/_test/context")
    def read_context() -> dict[str, str | None]:
        context = current_context()
        assert context is not None
        return {"request_id": context.request_id}

    with TestClient(app) as client:
        response = client.get("/_test/context", headers={REQUEST_ID_HEADER: "req-ctx"})

    assert response.json() == {"request_id": "req-ctx"}


def test_context_updated_in_sync_dependency_reaches_access_log(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    @app.get("/_test/identify")
    def identify() -> None:
        context = current_context()
        assert context is not None
        context.user_id = "user-1"

    caplog.handler.addFilter(RequestContextFilter())
    with caplog.at_level(logging.INFO, logger="app.access"), TestClient(app) as client:
        client.get("/_test/identify", headers={REQUEST_ID_HEADER: "req-dep"})

    record = next(r for r in caplog.records if r.name == "app.access")
    assert record.request_id == "req-dep"  # type: ignore[attr-defined]
    assert record.user_id == "user-1"  # type: ignore[attr-defined]
    assert current_context() is None


def test_unhandled_exception_returns_internal_error_with_request_id(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    @app.get("/_test/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    with TestClient(app) as client:
        response = client.get("/_test/boom", headers={REQUEST_ID_HEADER: "req-500"})

    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "req-500"
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "Internal server error.", "details": None}
    }
    assert any(r.exc_info for r in caplog.records)


def test_access_log_has_request_fields(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.access"):
        client.get("/openapi.json")

    record = next(r for r in caplog.records if r.name == "app.access")
    assert record.levelno == logging.INFO
    assert record.method == "GET"  # type: ignore[attr-defined]
    assert record.path == "/openapi.json"  # type: ignore[attr-defined]
    assert record.status == 200  # type: ignore[attr-defined]
    assert record.latency_ms >= 0  # type: ignore[attr-defined]


def test_health_check_access_log_is_debug_level(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="app.access"):
        client.get("/health/live")

    record = next(r for r in caplog.records if r.name == "app.access")
    assert record.levelno == logging.DEBUG
    assert record.path == "/health/live"  # type: ignore[attr-defined]
