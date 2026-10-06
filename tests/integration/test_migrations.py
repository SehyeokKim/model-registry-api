from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import URL, Engine, inspect

from app.db.base import Base
from tests.integration.conftest import alembic_config

EXPECTED_TABLES = {"organizations", "users", "models", "model_versions"}


def test_migrations_can_be_downgraded_and_upgraded_again(database_url: URL, engine: Engine) -> None:
    config = alembic_config(database_url)

    command.downgrade(config, "base")
    assert EXPECTED_TABLES.isdisjoint(inspect(engine).get_table_names())

    command.upgrade(config, "head")
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES


def test_orm_entities_match_migrated_schema(engine: Engine) -> None:
    """엔티티를 바꾸고 마이그레이션을 빠뜨리면 실패한다."""
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        diff = compare_metadata(context, Base.metadata)

    assert diff == []
