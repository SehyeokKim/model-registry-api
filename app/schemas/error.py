from typing import Any

from pydantic import BaseModel, Field

from app.core.errors import ERROR_CATALOG, ErrorCode, error_body


class ErrorDetail(BaseModel):
    code: ErrorCode = Field(description="에러 코드. 클라이언트는 이 값으로 분기한다.")
    message: str = Field(description="개발자용 영어 설명. 화면에 그대로 보여주지 않는다.")
    details: Any | None = Field(default=None, description="필드별 검증 오류 등 추가 정보")


class ErrorResponse(BaseModel):
    error: ErrorDetail


def error_responses(*codes: ErrorCode) -> dict[int | str, dict[str, Any]]:
    """엔드포인트가 낼 수 있는 에러 코드를 OpenAPI `responses`로 만든다.

    같은 HTTP 상태의 코드는 하나의 응답 아래 예시로 묶고, 설명에 발생 조건을 적는다.
    """
    responses: dict[int | str, dict[str, Any]] = {}
    for code in codes:
        spec = ERROR_CATALOG[code]
        response = responses.setdefault(
            spec.status,
            {
                "model": ErrorResponse,
                "description": "",
                "content": {"application/json": {"examples": {}}},
            },
        )
        line = f"`{code}`: {spec.condition}"
        response["description"] = f"{response['description']}\n\n{line}".strip()
        response["content"]["application/json"]["examples"][code.value] = {
            "summary": code.value,
            "value": error_body(code),
        }
    return responses
