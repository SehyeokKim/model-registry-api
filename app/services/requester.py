import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Requester:
    """고객 API 요청자. 요청 범위는 요청자가 속한 조직이다 (ADR 0006)."""

    user_id: uuid.UUID
    organization_id: uuid.UUID
