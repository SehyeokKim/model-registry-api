"""환경 변수 기반 애플리케이션 설정."""

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

# 로컬 개발용 기본값. compose와 운영 환경은 DATABASE_URL로 덮어쓴다.
DEFAULT_DATABASE_URL = "postgresql+psycopg://registry:registry@localhost:5432/registry"


class LogFormat(StrEnum):
    JSON = "json"
    TEXT = "text"


class Settings(BaseSettings):
    """환경 변수 목록과 기본값은 `.env.example`에 문서화한다."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    log_level: LogLevel = "INFO"
    log_format: LogFormat = LogFormat.JSON

    # DB 연결 예산: Pod당 최대 pool_size + max_overflow 연결 (ADR 0002)
    database_url: str = DEFAULT_DATABASE_URL
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout: float = Field(default=5.0, gt=0, description="커넥션을 기다리는 최대 시간(초)")
    db_connect_timeout: int = Field(default=5, ge=1, description="DB 접속 시도 최대 시간(초)")

    # migrate 서비스가 마이그레이션 후 데모 데이터를 넣을지 여부
    seed_demo_data: bool = True

    # 삭제된 모델을 복원할 수 있는 기간(일). purge_at = deleted_at + 이 기간
    model_restore_window_days: int = Field(default=30, ge=1)

    # 영구 삭제 커맨드가 한 트랜잭션에서 지우는 최대 모델 수 (ADR 0005)
    purge_batch_size: int = Field(default=500, ge=1)

    @field_validator("log_level", mode="before")
    @classmethod
    def _uppercase_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
