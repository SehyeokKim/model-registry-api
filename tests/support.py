"""테스트 공용 헬퍼."""

import uuid

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import LogFormat, Settings
from app.db.entities import MLModelVersion, ModelVersionStatus

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


def insert_version(
    engine: Engine,
    model_id: str,
    version_no: int = 1,
    status: ModelVersionStatus = ModelVersionStatus.PENDING,
) -> str:
    """버전을 DB에 직접 넣는다. 상태 전이 API를 거치지 않고 특정 상태의 버전이 필요할 때 쓴다."""
    version_id = uuid.uuid4()
    # 아티팩트 경로는 version_no가 아니라 UUID로 만든다.
    artifact_uri = (
        f"s3://model-artifacts/models/{model_id}/versions/{version_id}/"
        if status in (ModelVersionStatus.READY, ModelVersionStatus.ARCHIVED)
        else None
    )
    with Session(engine) as session, session.begin():
        session.add(
            MLModelVersion(
                id=version_id,
                model_id=uuid.UUID(model_id),
                version_no=version_no,
                created_by=CUSTOMER_A1,
                status=status,
                artifact_uri=artifact_uri,
            )
        )
    return str(version_id)
