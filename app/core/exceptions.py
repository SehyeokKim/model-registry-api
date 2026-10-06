"""도메인 예외. 서비스 계층은 HTTP를 모른 채 에러 코드로 실패를 알린다.

HTTP 상태는 에러 코드 카탈로그(`ERROR_CATALOG`)에서 정하고, 전역 핸들러가 공통 응답으로 바꾼다.
"""

from typing import Any

from app.core.errors import ERROR_CATALOG, ErrorCode


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.code = code
        self.message = message or ERROR_CATALOG[code].message
        self.details = details
        self.headers = headers
        super().__init__(self.message)

    @property
    def status_code(self) -> int:
        return ERROR_CATALOG[self.code].status
