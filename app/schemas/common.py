from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


# API 응답 시각은 DB 세션 시간대와 관계없이 UTC로 내보낸다. 예: 2026-10-06T03:00:00Z
UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]
