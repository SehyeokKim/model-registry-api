from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.entities import Organization, User, UserRole
from scripts.seed import DEMO_ORGANIZATIONS, DEMO_USERS, run, seed_demo_data


def _counts(engine: Engine) -> tuple[int, int]:
    with Session(engine) as session:
        return (
            session.scalar(select(func.count()).select_from(Organization)) or 0,
            session.scalar(select(func.count()).select_from(User)) or 0,
        )


def test_seed_inserts_demo_organizations_and_users(engine: Engine) -> None:
    seed_demo_data(engine)

    assert _counts(engine) == (len(DEMO_ORGANIZATIONS), len(DEMO_USERS))


def test_seed_is_idempotent(engine: Engine) -> None:
    seed_demo_data(engine)
    seed_demo_data(engine)

    assert _counts(engine) == (len(DEMO_ORGANIZATIONS), len(DEMO_USERS))


def test_seed_includes_internal_user_without_organization(engine: Engine) -> None:
    seed_demo_data(engine)

    with Session(engine) as session:
        internal_users = session.scalars(select(User).where(User.role != UserRole.CUSTOMER)).all()

    assert [user.organization_id for user in internal_users] == [None]


def test_seed_can_be_disabled(engine: Engine, settings: Settings) -> None:
    run(settings.model_copy(update={"seed_demo_data": False}))

    assert _counts(engine) == (0, 0)


def test_run_seeds_with_settings(engine: Engine, settings: Settings) -> None:
    run(settings)

    assert _counts(engine) == (len(DEMO_ORGANIZATIONS), len(DEMO_USERS))
