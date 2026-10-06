"""테스트 공용 헬퍼."""

from app.core.config import LogFormat, Settings


def make_settings(**overrides: object) -> Settings:
    """로컬 .env의 영향을 받지 않는 테스트용 설정."""
    values: dict[str, object] = {"log_level": "INFO", "log_format": LogFormat.JSON}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]
