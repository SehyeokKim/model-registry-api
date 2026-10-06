"""모델 API의 계약(요청자 식별, 에러 형식, OpenAPI 문서)을 DB 없이 확인한다."""

from fastapi.testclient import TestClient


def test_unparsable_body_uses_common_error_format(client: TestClient) -> None:
    response = client.post(
        "/api/v1/models",
        content="이름".encode("cp949"),
        headers={"Content-Type": "application/json", "X-User-Id": "x"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"


def test_missing_user_header_is_unauthorized(client: TestClient) -> None:
    response = client.get("/api/v1/models/00000000-0000-0000-0000-000000000999")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_non_uuid_user_header_is_unauthorized(client: TestClient) -> None:
    response = client.get(
        "/api/v1/models/00000000-0000-0000-0000-000000000999",
        headers={"X-User-Id": "not-a-uuid"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "X-User-Id must be a UUID."


class TestOpenApi:
    def test_user_header_is_registered_as_security_scheme(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()

        assert schema["components"]["securitySchemes"]["UserId"] == {
            "type": "apiKey",
            "in": "header",
            "name": "X-User-Id",
            "description": schema["components"]["securitySchemes"]["UserId"]["description"],
        }

    def test_model_endpoints_document_their_errors(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]

        expected = {
            ("/api/v1/models", "post"): {"201", "401", "403", "409", "422", "503"},
            ("/api/v1/models/{model_id}", "get"): {"200", "401", "403", "404", "422", "503"},
            ("/api/v1/models/{model_id}", "patch"): {
                "200",
                "401",
                "403",
                "404",
                "409",
                "422",
                "503",
            },
            ("/api/v1/models/{model_id}", "delete"): {"200", "401", "403", "404", "422", "503"},
        }
        for (path, method), statuses in expected.items():
            operation = paths[path][method]
            assert operation["summary"], (path, method)
            assert set(operation["responses"]) == statuses, (path, method)

    def test_same_status_errors_are_listed_as_examples(self, client: TestClient) -> None:
        patch = client.get("/openapi.json").json()["paths"]["/api/v1/models/{model_id}"]["patch"]

        examples = patch["responses"]["409"]["content"]["application/json"]["examples"]
        assert set(examples) == {"MODEL_NAME_CONFLICT", "TASK_TYPE_LOCKED"}
