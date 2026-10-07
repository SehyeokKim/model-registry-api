from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import AfterValidator


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


# API 응답 시각은 DB 세션 시간대와 관계없이 UTC로 내보낸다. 예: 2026-10-06T03:00:00Z
UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]


def check_storable_text(value: Any) -> Any:
    """PostgreSQL에 저장할 수 없는 문자를 거부한다.

    NUL(U+0000)은 text와 jsonb에 저장할 수 없고, 짝 없는 서로게이트(U+D800–U+DFFF)는
    UTF-8로 인코딩할 수 없다. 저장 단계에서 500이 되기 전에 입력 검증(422)으로 알린다.
    """
    if isinstance(value, str) and any(c == "\x00" or "\ud800" <= c <= "\udfff" for c in value):
        raise ValueError("Text must not contain NUL or unpaired surrogate characters")
    return value
