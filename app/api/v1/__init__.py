"""고객 API v1. 요청 범위는 요청자가 속한 조직이다."""

from fastapi import APIRouter

from app.api.v1 import model_versions, models

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(models.router)
api_router.include_router(model_versions.router)
