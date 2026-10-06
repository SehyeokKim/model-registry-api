import json
import logging
import sys

import pytest

from app.core.config import LogFormat, Settings
from app.core.logging import JsonFormatter, RequestContextFilter, configure_logging
from app.core.request_context import RequestContext, _current


def _make_record(msg: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _format_with_context(record: logging.LogRecord) -> dict[str, object]:
    RequestContextFilter().filter(record)
    return json.loads(JsonFormatter().format(record))


def test_json_log_has_common_fields_in_utc() -> None:
    payload = _format_with_context(_make_record())

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "hello"
    assert isinstance(payload["timestamp"], str)
    assert payload["timestamp"].endswith("Z")


def test_json_log_includes_request_context() -> None:
    token = _current.set(RequestContext(request_id="req-1", organization_id="org-1", user_id="u-1"))
    try:
        payload = _format_with_context(_make_record())
    finally:
        _current.reset(token)

    assert payload["request_id"] == "req-1"
    assert payload["organization_id"] == "org-1"
    assert payload["user_id"] == "u-1"


def test_json_log_context_fields_are_null_outside_request() -> None:
    payload = _format_with_context(_make_record())

    assert payload["request_id"] is None
    assert payload["organization_id"] is None
    assert payload["user_id"] is None


def test_json_log_includes_extra_fields_and_keeps_non_ascii() -> None:
    payload = _format_with_context(_make_record("모델 생성", status=201, path="/api/v1/models"))

    assert payload["message"] == "모델 생성"
    assert payload["status"] == 201
    assert payload["path"] == "/api/v1/models"


def test_json_log_drops_uvicorn_color_message() -> None:
    payload = _format_with_context(_make_record(color_message="\x1b[36mhello\x1b[0m"))

    assert "color_message" not in payload


def test_json_log_includes_exception() -> None:
    try:
        raise ValueError("bad")
    except ValueError:
        record = logging.LogRecord(
            "app.test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )

    payload = _format_with_context(record)

    assert "ValueError: bad" in str(payload["exc_info"])


def test_configure_logging_does_not_duplicate_handlers() -> None:
    configure_logging("INFO", LogFormat.JSON)
    configure_logging("DEBUG", LogFormat.TEXT)

    app_handlers = [h for h in logging.getLogger().handlers if h.get_name() == "app"]
    assert len(app_handlers) == 1
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("uvicorn.access").disabled


@pytest.mark.parametrize(("raw", "expected"), [("info", "INFO"), ("Debug", "DEBUG")])
def test_log_level_is_case_insensitive(raw: str, expected: str) -> None:
    assert Settings(log_level=raw).log_level == expected  # type: ignore[arg-type]
