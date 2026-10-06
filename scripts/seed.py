"""멱등 데모 데이터 시드. migrate 서비스가 마이그레이션 직후 실행한다.

스키마 마이그레이션과 분리해, 운영 DB에는 `SEED_DEMO_DATA=false`로 넣지 않는다 (ADR 0001).
고정 UUID와 `ON CONFLICT DO NOTHING`을 써서 여러 번 실행해도 결과가 같다.

    python -m scripts.seed
"""

import logging
import uuid

from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.entities import Organization, User, UserRole
from app.db.session import create_db_engine

logger = logging.getLogger("scripts.seed")

ORGANIZATION_A_ID = uuid.UUID("00000000-0000-0000-0000-000000000100")
ORGANIZATION_B_ID = uuid.UUID("00000000-0000-0000-0000-000000000200")

# 조직 내 공유, 조직 간 격리, 내부 사용자 차단을 바로 확인할 수 있는 구성 (CLAUDE.md 참고)
DEMO_ORGANIZATIONS = [
    {"id": ORGANIZATION_A_ID, "name": "Demo Manufacturing"},
    {"id": ORGANIZATION_B_ID, "name": "Demo Energy"},
]
DEMO_USERS = [
    {
        "id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "organization_id": ORGANIZATION_A_ID,
        "email": "customer-a1@example.com",
        "name": "Customer A1",
        "role": UserRole.CUSTOMER,
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-000000000002"),
        "organization_id": ORGANIZATION_A_ID,
        "email": "customer-a2@example.com",
        "name": "Customer A2",
        "role": UserRole.CUSTOMER,
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-000000000003"),
        "organization_id": ORGANIZATION_B_ID,
        "email": "customer-b1@example.com",
        "name": "Customer B1",
        "role": UserRole.CUSTOMER,
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-000000000004"),
        "organization_id": None,
        "email": "ml-engineer@example.com",
        "name": "ML Engineer",
        "role": UserRole.ML_ENGINEER,
    },
]


def seed_demo_data(engine: Engine) -> None:
    """데모 조직과 사용자를 넣는다. 이미 있는 행은 건너뛴다."""
    # 여러 행 INSERT는 드라이버가 rowcount를 돌려주지 않으므로,
    # 실제로 들어간 행을 RETURNING으로 센다.
    with engine.begin() as connection:
        organizations = connection.execute(
            insert(Organization)
            .values(DEMO_ORGANIZATIONS)
            .on_conflict_do_nothing()
            .returning(Organization.id)
        ).all()
        users = connection.execute(
            insert(User).values(DEMO_USERS).on_conflict_do_nothing().returning(User.id)
        ).all()
    logger.info(
        "Demo data seeded",
        extra={"organizations_inserted": len(organizations), "users_inserted": len(users)},
    )


def run(settings: Settings) -> None:
    if not settings.seed_demo_data:
        logger.info("Demo data seeding skipped (SEED_DEMO_DATA=false)")
        return

    engine = create_db_engine(settings)
    try:
        seed_demo_data(engine)
    finally:
        engine.dispose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    run(settings)


if __name__ == "__main__":
    main()
