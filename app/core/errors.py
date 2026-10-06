"""에러 코드 정의. 코드를 추가하거나 바꾸면 README의 에러 코드 표도 함께 고친다.

클라이언트는 `code`로 분기하고 화면 문구를 현지화한다. 따라서 코드는 한 번 정하면 바꾸지 않는다.
"""

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# DB를 일시적으로 쓸 수 없을 때 클라이언트에게 알려주는 재시도 대기 시간(초)
RETRY_AFTER_SECONDS = 5


def error_body(code: ErrorCode, message: str, details: Any = None) -> dict[str, Any]:
    """공통 에러 응답 본문: {"error": {"code", "message", "details"}}"""
    return {"error": {"code": code.value, "message": message, "details": details}}
