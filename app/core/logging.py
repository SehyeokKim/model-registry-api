"""로깅 설정. 운영은 JSON 한 줄 로그, 로컬 개발은 텍스트 로그를 쓴다."""

import json
import logging
import sys
import time
from datetime import UTC, datetime

from app.core.config import LogFormat, LogLevel
from app.core.request_context import current_context

_HANDLER_NAME = "app"
_CONTEXT_FIELDS = ("request_id", "organization_id", "user_id")

# LogRecord 기본 속성. 이 외의 속성은 `extra`로 넘긴 필드로 보고 JSON에 포함한다.
# `color_message`는 uvicorn이 터미널 색상용으로 붙이는 값이라 제외한다.
_RESERVED_ATTRS = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None)).keys()
) | {"message", "asctime", "taskName", "color_message"}


class RequestContextFilter(logging.Filter):
    """로그 레코드에 현재 요청의 컨텍스트 필드를 붙인다."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = current_context()
        for field in _CONTEXT_FIELDS:
            setattr(record, field, getattr(context, field) if context else None)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": _utc_iso8601(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _CONTEXT_FIELDS:
            payload[field] = getattr(record, field, None)
        for key, value in vars(record).items():
            if key not in _RESERVED_ATTRS and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _utc_iso8601(created: float) -> str:
    timestamp = datetime.fromtimestamp(created, tz=UTC).isoformat(timespec="milliseconds")
    return timestamp.replace("+00:00", "Z")


def _text_formatter() -> logging.Formatter:
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    formatter.converter = time.gmtime
    return formatter


def configure_logging(level: LogLevel, log_format: LogFormat) -> None:
    """루트 로거에 애플리케이션 핸들러를 하나만 설치한다. 여러 번 호출해도 중복되지 않는다."""
    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(_HANDLER_NAME)
    handler.addFilter(RequestContextFilter())
    handler.setFormatter(JsonFormatter() if log_format is LogFormat.JSON else _text_formatter())

    root = logging.getLogger()
    for existing in [h for h in root.handlers if h.get_name() == _HANDLER_NAME]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn 로그도 같은 형식으로 남기고, 요청 로그는 미들웨어의 접근 로그로 대신한다.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.handlers.clear()
    uvicorn_access.propagate = False
    uvicorn_access.disabled = True
