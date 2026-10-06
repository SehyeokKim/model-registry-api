from fastapi.testclient import TestClient

from app.core.errors import ERROR_CATALOG, ErrorCode
from app.core.request_context import REQUEST_ID_HEADER


def test_every_error_code_is_in_catalog() -> None:
    assert set(ERROR_CATALOG) == set(ErrorCode)


def test_unknown_path_uses_common_error_format(client: TestClient) -> None:
    response = client.get("/no-such-path", headers={REQUEST_ID_HEADER: "req-404"})

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "NOT_FOUND",
            "message": "The requested path does not exist.",
            "details": None,
        }
    }
    assert response.headers[REQUEST_ID_HEADER] == "req-404"


def test_unsupported_method_uses_common_error_format(client: TestClient) -> None:
    response = client.put("/health/live")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert response.headers["Allow"] == "GET"
