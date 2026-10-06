"""예외를 공통 에러 응답 `{"error": {"code", "message", "details"}}`으로 바꾸는 전역 핸들러.

처리되지 않은 예외(500)는 요청 ID를 붙이기 위해 RequestContextMiddleware가 처리한다.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import ERROR_CATALOG, RETRY_AFTER_SECONDS, ErrorCode, error_body
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

# 라우팅 단계에서 프레임워크가 내는 오류도 공통 형식으로 응답한다.
_FRAMEWORK_ERROR_CODES = {
    400: ErrorCode.BAD_REQUEST,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
}


def _error_response(
    code: ErrorCode,
    message: str | None = None,
    *,
    details: object = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=ERROR_CATALOG[code].status,
        content=error_body(code, message, details),
        headers=headers,
    )


async def _app_error(request: Request, exc: AppError) -> JSONResponse:
    return _error_response(exc.code, exc.message, details=exc.details, headers=exc.headers)


async def _request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # 입력값(input)은 개인정보가 섞일 수 있어 응답에 되돌려 주지 않는다.
    details = [
        {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return _error_response(ErrorCode.VALIDATION_ERROR, details=details)


async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
    code = _FRAMEWORK_ERROR_CODES.get(exc.status_code)
    if code is None:
        return await http_exception_handler(request, exc)
    return _error_response(code, headers=exc.headers)


async def _database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    """커넥션 풀 대기 초과, DB 접속 실패 등 일시적인 DB 장애를 503으로 알린다 (ADR 0002).

    요청이 쌓여 스레드풀이 막히기 전에 빠르게 실패하고, 클라이언트가 재시도하게 한다.
    """
    logger.warning("Database unavailable: %s", type(exc).__name__, exc_info=exc)
    return _error_response(
        ErrorCode.SERVICE_UNAVAILABLE, headers={"Retry-After": str(RETRY_AFTER_SECONDS)}
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _request_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_error)  # type: ignore[arg-type]
    for exc_class in (PoolTimeoutError, OperationalError, InterfaceError):
        app.add_exception_handler(exc_class, _database_unavailable)
