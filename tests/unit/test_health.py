import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.request_context import REQUEST_ID_HEADER
from tests.support import make_settings

# 접속을 즉시 거부하는 주소. DB 없이 장애 상황을 재현한다.
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://registry:registry@127.0.0.1:1/registry"


def test_live_returns_ok(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_live_is_documented_in_openapi(client: TestClient) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/health/live"]["get"]

    assert operation["summary"] == "프로세스 생존 확인"
    assert operation["tags"] == ["Health"]


class TestReadyWithoutDatabase:
    @pytest.fixture
    def settings(self) -> Settings:
        return make_settings(database_url=UNREACHABLE_DATABASE_URL, db_connect_timeout=1)

    def test_live_still_returns_ok(self, client: TestClient) -> None:
        assert client.get("/health/live").status_code == 200

    def test_ready_returns_service_unavailable(self, client: TestClient) -> None:
        response = client.get("/health/ready", headers={REQUEST_ID_HEADER: "req-ready"})

        assert response.status_code == 503
        assert response.headers["Retry-After"] == "5"
        assert response.headers[REQUEST_ID_HEADER] == "req-ready"
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    def test_ready_documents_503_response(self, client: TestClient) -> None:
        operation = client.get("/openapi.json").json()["paths"]["/health/ready"]["get"]

        assert "503" in operation["responses"]
