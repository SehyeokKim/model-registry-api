"""FastAPI 애플리케이션 팩토리."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.api.v1 import api_router
from app.core.config import Settings, get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.request_context import RequestContextMiddleware
from app.db.session import create_db_engine, create_session_factory

API_DESCRIPTION = """
AutoML 플랫폼의 모델 관리 API. 고객 조직의 모델과 모델 버전의 생명주기를 관리한다.

## 요청자 식별
인증은 API Gateway가 처리한다고 가정하고, 요청자는 `X-User-Id` 헤더로 식별한다.
오른쪽 위 **Authorize** 버튼에 사용자 ID를 한 번 입력하면 모든 요청에 붙는다.

| 데모 사용자 | ID | 확인할 수 있는 것 |
|---|---|---|
| 조직 A 고객 1 | `00000000-0000-0000-0000-000000000001` | 기본 사용자 |
| 조직 A 고객 2 | `00000000-0000-0000-0000-000000000002` | 같은 조직의 모델 공유 |
| 조직 B 고객 | `00000000-0000-0000-0000-000000000003` | 다른 조직의 모델은 404 |
| ML Engineer (내부) | `00000000-0000-0000-0000-000000000004` | 고객 API는 403 |

## 공통 규칙
- 에러 응답은 `{"error": {"code", "message", "details"}}` 형식이다. 클라이언트는 `code`로 분기한다.
- 모든 응답에는 `X-Request-ID` 헤더가 붙는다. 문의 시 이 값으로 요청 로그를 찾는다.
- 시각은 UTC ISO 8601 형식(`2026-10-06T03:00:00Z`)이다.
"""

OPENAPI_TAGS = [
    {"name": "Models", "description": "모델 생명주기 (생성, 조회, 수정, 소프트 삭제, 목록, 복원)"},
    {"name": "Model Versions", "description": "모델 버전 (학습 결과물 단위, 생성 후 불변)"},
    {"name": "Health", "description": "운영 엔드포인트 (Kubernetes probe, compose healthcheck)"},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="Model Registry API",
        version="0.1.0",
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    app.state.settings = settings
    # 엔진은 생성 시점에 접속하지 않으므로 DB 없이도 앱은 기동된다.
    app.state.engine = create_db_engine(settings)
    app.state.session_factory = create_session_factory(app.state.engine)

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router)
    app.include_router(health.router)
    return app


app = create_app()
