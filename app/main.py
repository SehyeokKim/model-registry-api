"""FastAPI 애플리케이션 팩토리."""

from fastapi import FastAPI

from app.api import health
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.request_context import RequestContextMiddleware

API_DESCRIPTION = """
AutoML 플랫폼의 모델 관리 API. 고객 조직의 모델과 모델 버전의 생명주기를 관리한다.

- 모든 응답에는 `X-Request-ID` 헤더가 붙는다. 문의 시 이 값으로 요청 로그를 찾는다.
- 시각은 UTC ISO 8601 형식(`2026-10-06T03:00:00Z`)이다.
"""

OPENAPI_TAGS = [
    {"name": "Health", "description": "운영 엔드포인트 (Kubernetes probe, compose healthcheck)"},
]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="Model Registry API",
        version="0.1.0",
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    return app


app = create_app()
