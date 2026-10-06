"""예외를 공통 에러 응답으로 바꾸는 전역 핸들러."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core.errors import RETRY_AFTER_SECONDS, ErrorCode, error_body

logger = logging.getLogger(__name__)


async def _database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    """커넥션 풀 대기 초과, DB 접속 실패 등 일시적인 DB 장애를 503으로 알린다 (ADR 0002).

    요청이 쌓여 스레드풀이 막히기 전에 빠르게 실패하고, 클라이언트가 재시도하게 한다.
    """
    logger.warning("Database unavailable: %s", type(exc).__name__, exc_info=exc)
    return JSONResponse(
        status_code=503,
        content=error_body(
            ErrorCode.SERVICE_UNAVAILABLE, "Database is temporarily unavailable. Retry later."
        ),
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )


def register_exception_handlers(app: FastAPI) -> None:
    for exc_class in (PoolTimeoutError, OperationalError, InterfaceError):
        app.add_exception_handler(exc_class, _database_unavailable)
