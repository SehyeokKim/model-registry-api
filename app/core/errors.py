"""에러 코드 카탈로그. 코드별 HTTP 상태, 발생 조건, 기본 메시지를 이 한 곳에서 정한다.

전역 예외 핸들러, OpenAPI 문서, README의 에러 코드 표가 모두 이 카탈로그를 기준으로 한다.
클라이언트는 `code`로 분기하고 화면 문구를 현지화한다. 따라서 코드는 한 번 정하면 바꾸지 않는다.
`message`는 개발자용 영어 문장이며 화면에 그대로 보여주지 않는다.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    BAD_REQUEST = "BAD_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    MODEL_NAME_CONFLICT = "MODEL_NAME_CONFLICT"
    TASK_TYPE_LOCKED = "TASK_TYPE_LOCKED"
    MODEL_NOT_DELETED = "MODEL_NOT_DELETED"
    MODEL_PURGED = "MODEL_PURGED"
    INVALID_PARENT_VERSION = "INVALID_PARENT_VERSION"
    MODEL_VERSION_CONFLICT = "MODEL_VERSION_CONFLICT"
    MODEL_VERSION_NOT_FOUND = "MODEL_VERSION_NOT_FOUND"
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"
    EMPTY_UPDATE = "EMPTY_UPDATE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    status: int
    condition: str
    message: str


ERROR_CATALOG: dict[ErrorCode, ErrorSpec] = {
    ErrorCode.UNAUTHORIZED: ErrorSpec(
        401,
        "`X-User-Id`가 없거나, UUID 형식이 아니거나, 존재하지 않는 사용자",
        "A valid X-User-Id header is required.",
    ),
    ErrorCode.FORBIDDEN: ErrorSpec(
        403,
        "조직에 속하지 않은 내부 사용자가 고객 API를 호출하거나, 고객이 내부 API를 호출",
        "This user is not allowed to call this API.",
    ),
    ErrorCode.BAD_REQUEST: ErrorSpec(
        400,
        "요청 본문을 해석할 수 없음 (UTF-8이 아닌 인코딩 등)",
        "The request body could not be parsed.",
    ),
    ErrorCode.NOT_FOUND: ErrorSpec(404, "정의되지 않은 경로", "The requested path does not exist."),
    ErrorCode.METHOD_NOT_ALLOWED: ErrorSpec(
        405, "경로가 지원하지 않는 HTTP 메서드", "The method is not allowed for this path."
    ),
    ErrorCode.MODEL_NOT_FOUND: ErrorSpec(
        404,
        "모델이 없거나, 삭제되었거나(복원 제외), 다른 조직 소유이거나, 영구 삭제됨 "
        "(존재 여부를 드러내지 않음)",
        "Model not found.",
    ),
    ErrorCode.MODEL_NAME_CONFLICT: ErrorSpec(
        409,
        "같은 조직에 같은 이름의 활성 모델이 있음",
        "An active model with the same name already exists in the organization.",
    ),
    ErrorCode.TASK_TYPE_LOCKED: ErrorSpec(
        409,
        "버전이 있는 모델의 `task_type` 변경 시도",
        "task_type cannot be changed after a version has been created.",
    ),
    ErrorCode.MODEL_NOT_DELETED: ErrorSpec(
        409, "삭제되지 않은 모델의 복원 시도", "The model is not deleted."
    ),
    ErrorCode.MODEL_PURGED: ErrorSpec(
        410,
        "복원 기한(`purge_at`)이 지나 복원할 수 없는 모델 (영구 삭제 대기 중)",
        "The restore deadline has passed and the model is pending permanent deletion.",
    ),
    ErrorCode.INVALID_PARENT_VERSION: ErrorSpec(
        422,
        "부모 버전이 없거나, 다른 모델의 버전이거나, `READY` 상태가 아님",
        "The parent version must be a READY version of the same model.",
    ),
    ErrorCode.MODEL_VERSION_CONFLICT: ErrorSpec(
        409,
        "버전 번호가 동시에 생성된 다른 버전과 겹침 (다시 시도하면 된다)",
        "A version with the same number was created concurrently. Retry the request.",
    ),
    ErrorCode.MODEL_VERSION_NOT_FOUND: ErrorSpec(
        404,
        "내부 API에서 버전이 없거나, 소속 모델이 영구 삭제됨 (복원 기한 경과 포함)",
        "Model version not found.",
    ),
    ErrorCode.INVALID_STATUS_TRANSITION: ErrorSpec(
        409,
        "허용되지 않은 버전 상태 전이 (같은 상태로의 전이 포함)",
        "The status transition is not allowed.",
    ),
    ErrorCode.EMPTY_UPDATE: ErrorSpec(
        422, "변경할 필드가 없는 수정 요청(`{}`)", "At least one field must be provided."
    ),
    ErrorCode.VALIDATION_ERROR: ErrorSpec(
        422, "요청 형식이나 값 검증 실패 (`details`에 필드별 오류)", "Request validation failed."
    ),
    ErrorCode.INTERNAL_ERROR: ErrorSpec(500, "처리되지 않은 서버 오류", "Internal server error."),
    ErrorCode.SERVICE_UNAVAILABLE: ErrorSpec(
        503,
        "DB 커넥션 풀 대기 시간 초과 또는 DB 접속 실패 (`Retry-After` 헤더 포함)",
        "Database is temporarily unavailable. Retry later.",
    ),
}

# DB를 일시적으로 쓸 수 없을 때 클라이언트에게 알려주는 재시도 대기 시간(초)
RETRY_AFTER_SECONDS = 5


def error_body(code: ErrorCode, message: str | None = None, details: Any = None) -> dict[str, Any]:
    """공통 에러 응답 본문: {"error": {"code", "message", "details"}}"""
    return {
        "error": {
            "code": code.value,
            "message": message or ERROR_CATALOG[code].message,
            "details": details,
        }
    }
