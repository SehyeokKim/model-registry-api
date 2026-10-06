"""내부 API v1. 학습 파이프라인이 호출하며, 운영에서는 API Gateway에 노출하지 않는다.

내부 사용자만 호출할 수 있고 조직 범위 제한이 없다 (ADR 0006).
"""

from fastapi import APIRouter

from app.api.internal import model_versions

internal_router = APIRouter(prefix="/internal/v1")
internal_router.include_router(model_versions.router)
