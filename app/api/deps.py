"""라우터 공통 의존성: DB 세션, 요청자 식별, 서비스 생성."""

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.core.errors import ErrorCode
from app.core.exceptions import AppError
from app.core.request_context import current_context
from app.db.entities import User
from app.repositories.user_repository import UserRepository
from app.services.model_service import ModelService
from app.services.requester import Requester

USER_ID_HEADER = "X-User-Id"

# Swagger의 Authorize 버튼으로 한 번 입력하면 모든 요청에 붙는다.
# 인증 자체는 이 서비스 범위 밖이며, API Gateway가 검증한 사용자 ID를 전달한다고 가정한다.
_user_id_header = APIKeyHeader(
    name=USER_ID_HEADER,
    scheme_name="UserId",
    description=(
        "요청자 사용자 ID(UUID). 데모 사용자: `00000000-0000-0000-0000-000000000001` "
        "(조직 A 고객). 인증은 API Gateway가 처리한다고 가정한다."
    ),
    auto_error=False,
)


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_current_user(
    session: SessionDep, raw_user_id: Annotated[str | None, Security(_user_id_header)]
) -> User:
    if raw_user_id is None:
        raise AppError(ErrorCode.UNAUTHORIZED, f"{USER_ID_HEADER} header is required.")
    try:
        user_id = uuid.UUID(raw_user_id)
    except ValueError:
        raise AppError(ErrorCode.UNAUTHORIZED, f"{USER_ID_HEADER} must be a UUID.") from None

    with session.begin():
        user = UserRepository(session).get(user_id)
    if user is None:
        raise AppError(ErrorCode.UNAUTHORIZED, "Unknown user.")

    # 이후 이 요청의 모든 로그에 조직과 사용자가 붙는다.
    context = current_context()
    if context is not None:
        context.user_id = str(user.id)
        context.organization_id = str(user.organization_id) if user.organization_id else None
    return user


def get_customer(user: Annotated[User, Depends(get_current_user)]) -> Requester:
    """고객 API(/api/v1)는 조직에 속한 사용자만 호출할 수 있다 (ADR 0006)."""
    if user.organization_id is None:
        raise AppError(ErrorCode.FORBIDDEN, "Internal users cannot call the customer API.")
    return Requester(user_id=user.id, organization_id=user.organization_id)


CustomerDep = Annotated[Requester, Depends(get_customer)]


def get_model_service(request: Request, session: SessionDep) -> ModelService:
    return ModelService(
        session, restore_window_days=request.app.state.settings.model_restore_window_days
    )


ModelServiceDep = Annotated[ModelService, Depends(get_model_service)]
