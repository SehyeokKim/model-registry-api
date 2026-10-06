"""테스트 공용 헬퍼."""

import uuid

from app.core.config import LogFormat, Settings

# scripts/seed.py의 데모 데이터와 같은 ID
CUSTOMER_A1 = uuid.UUID("00000000-0000-0000-0000-000000000001")
CUSTOMER_A2 = uuid.UUID("00000000-0000-0000-0000-000000000002")
CUSTOMER_B1 = uuid.UUID("00000000-0000-0000-0000-000000000003")
ML_ENGINEER = uuid.UUID("00000000-0000-0000-0000-000000000004")
ORGANIZATION_A = uuid.UUID("00000000-0000-0000-0000-000000000100")


def make_settings(**overrides: object) -> Settings:
    """로컬 .env의 영향을 받지 않는 테스트용 설정."""
    values: dict[str, object] = {"log_level": "INFO", "log_format": LogFormat.JSON}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def as_user(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-Id": str(user_id)}
