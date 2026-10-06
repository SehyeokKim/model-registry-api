import base64
import json
import uuid
from datetime import UTC, datetime

import pytest

from app.schemas.pagination import InvalidCursorError, decode_cursor, encode_cursor


def _raw_cursor(payload: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def test_cursor_round_trip_keeps_microseconds_and_timezone() -> None:
    created_at = datetime(2026, 10, 6, 3, 0, 0, 123456, tzinfo=UTC)
    model_id = uuid.uuid4()

    assert decode_cursor(encode_cursor(created_at, model_id)) == (created_at, model_id)


def test_cursor_is_url_safe() -> None:
    cursor = encode_cursor(datetime.now(UTC), uuid.uuid4())

    assert set(cursor) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "not base64!",
        "커서",
        base64.urlsafe_b64encode(b"not json").decode(),
        _raw_cursor([1, 2]),
        _raw_cursor({"c": "2026-10-06T03:00:00+00:00"}),
        _raw_cursor({"c": "yesterday", "i": str(uuid.uuid4())}),
        _raw_cursor({"c": "2026-10-06T03:00:00+00:00", "i": "not-a-uuid"}),
        _raw_cursor({"c": 1, "i": 2}),
        # 시간대가 없는 시각은 비교 기준이 모호하므로 받지 않는다.
        _raw_cursor({"c": "2026-10-06T03:00:00", "i": str(uuid.uuid4())}),
    ],
)
def test_malformed_cursor_is_rejected(cursor: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor)
