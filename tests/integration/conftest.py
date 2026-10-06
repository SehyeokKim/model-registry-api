"""실제 PostgreSQL을 쓰는 통합 테스트 기반 (ADR 0004).

- 개발 DB와 분리된 테스트 DB(`registry_test`)를 쓴다.
  이름이 `_test`로 끝나지 않으면 시작하지 않는다.
- 스키마는 `Base.metadata.create_all()`이 아니라 Alembic 마이그레이션으로 만든다.
- 테스트마다 실제로 커밋하고, 끝나면 모든 테이블을 TRUNCATE한다.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Engine, create_engine, make_url, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import Base
from tests.support import make_settings

ROOT_DIR = Path(__file__).resolve().parents[2]


def _test_database_url() -> URL:
    default = (
        "postgresql+psycopg://registry:registry@localhost:"
        f"{os.environ.get('DB_PORT', '5432')}/registry_test"
    )
    url = make_url(os.environ.get("TEST_DATABASE_URL", default))
    if not url.database or not url.database.endswith("_test"):
        pytest.exit(
            f"TEST_DATABASE_URL must point to a database ending with '_test': {url.database!r}",
            returncode=2,
        )
    return url


def _create_database_if_missing(url: URL) -> None:
    admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
            ).scalar()
            if not exists:
                # DB 이름은 위에서 검증한 값이며, 식별자는 바인드 파라미터로 넘길 수 없다.
                connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        admin_engine.dispose()


def alembic_config(url: URL) -> Config:
    config = Config(str(ROOT_DIR / "alembic.ini"))
    # configparser 보간 문자(%)가 비밀번호에 있어도 깨지지 않게 이스케이프한다.
    config.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    config.attributes["configure_logging"] = False
    return config


@pytest.fixture(scope="session")
def database_url() -> URL:
    url = _test_database_url()
    _create_database_if_missing(url)
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="session")
def engine(database_url: URL) -> Iterator[Engine]:
    test_engine = create_engine(database_url)
    yield test_engine
    test_engine.dispose()


@pytest.fixture(autouse=True)
def _truncate_tables(engine: Engine) -> Iterator[None]:
    yield
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def settings(database_url: URL) -> Settings:
    return make_settings(database_url=database_url.render_as_string(hide_password=False))
