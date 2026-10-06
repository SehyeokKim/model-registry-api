from typing import Any

from pydantic import BaseModel, Field

from app.core.errors import ErrorCode


class ErrorDetail(BaseModel):
    code: ErrorCode = Field(description="에러 코드. 클라이언트는 이 값으로 분기한다.")
    message: str = Field(description="개발자용 영어 설명. 화면에 그대로 보여주지 않는다.")
    details: Any | None = Field(default=None, description="필드별 검증 오류 등 추가 정보")


class ErrorResponse(BaseModel):
    error: ErrorDetail
