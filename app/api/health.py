"""운영 엔드포인트. API 버전과 무관하게 경로를 고정한다."""

from fastapi import APIRouter, Request
from sqlalchemy import Engine, text

from app.core.errors import ErrorCode
from app.schemas.error import error_responses
from app.schemas.health import HealthResponse

router = APIRouter(prefix="/health", tags=["Health"])


@router.get(
    "/live",
    summary="프로세스 생존 확인",
    description=(
        "API 프로세스가 요청을 처리할 수 있는지 확인한다. DB 등 외부 의존성은 확인하지 않는다. "
        "Kubernetes liveness probe가 사용한다. 실패하면 컨테이너를 재시작한다."
    ),
    response_model=HealthResponse,
)
def live() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    summary="요청 처리 준비 확인",
    description=(
        "DB 연결까지 확인한다. Kubernetes readiness probe와 compose healthcheck가 사용한다. "
        "실패하면 트래픽 대상에서 잠시 빠지며, 컨테이너는 재시작하지 않는다."
    ),
    response_model=HealthResponse,
    responses=error_responses(ErrorCode.SERVICE_UNAVAILABLE),
)
def ready(request: Request) -> HealthResponse:
    engine: Engine = request.app.state.engine
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return HealthResponse(status="ok")
