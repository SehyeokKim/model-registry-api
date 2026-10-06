"""목록 조회 keyset 커서.

클라이언트에는 내용을 해석하지 않는 불투명 문자열로 준다. 그래야 정렬 키나 형식을 바꿔도
클라이언트를 고치지 않아도 된다. 클라이언트는 받은 `next_cursor`를 그대로 다시 보내기만 한다.
"""

import base64
import binascii
import json
import uuid
from datetime import datetime


class InvalidCursorError(ValueError):
    pass


def encode_cursor(created_at: datetime, model_id: uuid.UUID) -> str:
    payload = json.dumps({"c": created_at.isoformat(), "i": str(model_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """`encode_cursor`가 만든 커서를 (created_at, id)로 되돌린다.

    형식이 틀리면 InvalidCursorError를 낸다.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        created_at = datetime.fromisoformat(data["c"])
        model_id = uuid.UUID(data["i"])
    except (binascii.Error, UnicodeError, ValueError, KeyError, TypeError, AttributeError):
        raise InvalidCursorError("Invalid cursor.") from None
    if created_at.tzinfo is None:
        raise InvalidCursorError("Invalid cursor.")
    return created_at, model_id
