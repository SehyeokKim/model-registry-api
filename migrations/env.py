"""Alembic 실행 환경.

DB 접속 정보는 alembic.ini가 아니라 앱 설정(DATABASE_URL)에서 읽는다.
테스트처럼 `sqlalchemy.url`을 직접 지정하면 그 값을 우선한다.
"""

from alembic import context
from sqlalchemy import create_engine, pool

import app.db.entities  # noqa: F401  모든 엔티티를 metadata에 등록한다.
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.base import Base

config = context.config
settings = get_settings()

# migrate 서비스의 로그도 API와 같은 형식으로 남긴다.
if config.attributes.get("configure_logging", True):
    configure_logging(settings.log_level, settings.log_format)

target_metadata = Base.metadata


def _database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or settings.database_url


def run_migrations_offline() -> None:
    """DB 접속 없이 SQL 스크립트만 출력한다 (`alembic upgrade head --sql`)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
