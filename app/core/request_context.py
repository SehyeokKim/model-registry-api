"""요청 범위 컨텍스트(요청 ID, 조직 ID, 사용자 ID)와 이를 설정하는 ASGI 미들웨어."""

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import ErrorCode, error_body

REQUEST_ID_HEADER = "X-Request-ID"

# 상위 시스템이 보낸 요청 ID는 이 형식일 때만 그대로 쓴다.
# 로그 오염과 헤더 주입을 막기 위해 허용 문자와 길이를 제한한다.
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")

access_logger = logging.getLogger("app.access")
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RequestContext:
    request_id: str
    organization_id: str | None = None
    user_id: str | None = None


# 값 대신 가변 객체를 담는다. 동기 의존성은 복사된 컨텍스트의 스레드에서 실행되므로,
# 그 안에서 ContextVar.set()을 하면 미들웨어의 접근 로그에 반영되지 않는다.
# 같은 객체의 필드를 바꾸면 모든 복사본에서 같은 값을 본다.
_current: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def current_context() -> RequestContext | None:
    return _current.get()


def resolve_request_id(value: str | None) -> str:
    """유효한 요청 ID는 그대로 쓰고, 없거나 형식이 맞지 않으면 새로 만든다."""
    if value is not None and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return str(uuid.uuid4())


_INTERNAL_ERROR_BODY = json.dumps(
    error_body(ErrorCode.INTERNAL_ERROR, "Internal server error.")
).encode()


class RequestContextMiddleware:
    """요청 컨텍스트를 만들고, 응답에 요청 ID를 붙이고, 요청당 접근 로그를 남긴다.

    `BaseHTTPMiddleware`는 컨텍스트 전파와 응답 스트리밍에 제약이 있어 순수 ASGI로 구현한다.
    처리되지 않은 예외도 이 미들웨어에서 500 응답으로 바꿔, 에러 응답에도 요청 ID가 붙게 한다.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        context = RequestContext(
            request_id=resolve_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        )
        token = _current.set(context)
        started_at = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = context.request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception("Unhandled exception while processing request")
            if response_started:
                raise
            await self._send_internal_error(send_with_request_id)
        finally:
            access_logger.info(
                "Request completed",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status_code,
                    "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
                },
            )
            _current.reset(token)

    @staticmethod
    async def _send_internal_error(send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_INTERNAL_ERROR_BODY)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _INTERNAL_ERROR_BODY})
