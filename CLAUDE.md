# CLAUDE.md

이 파일은 AI 코딩 도구(Claude Code)가 이 저장소에서 작업할 때 따르는 프로젝트 규칙이다.

## 프로젝트 개요
대상 플랫폼은 고객이 CSV/Excel 데이터를 업로드하고, 선택한 데이터로 GPU 학습을 요청하고, 버전 관리된 모델로 추론하는 AutoML 서비스다.
이 저장소는 플랫폼의 **모델 관리 API 서버**로, 모델(`models`)과 모델 버전(`model_versions`)의 생명주기를 관리한다.

- 전체 데이터 모델과 설계 의도: `docs/erd.md`
- 주요 설계 결정 기록: `docs/adr/`

전체 데이터 모델 (자동 로드):
@docs/erd.md

## 운영 요구사항
1. 저장소 루트에서 `docker compose up -d` 한 번으로 DB와 API 서버가 기동된다.
2. 추가 설정 없이 동작한다. 모든 환경 변수는 compose에 기본값을 두고, `.env.example`로 목록을 문서화한다.
3. 기동 시 DB 마이그레이션이 자동 실행된다. 마이그레이션은 API 컨테이너와 분리된 일회성 `migrate` 서비스가 수행하고, API는 그 서비스가 성공적으로 끝난 뒤 시작한다 (`depends_on: condition: service_completed_successfully`).
   - 이유: API 레플리카가 여러 개일 때 각자 마이그레이션을 돌리면 경쟁 상태가 생긴다. Kubernetes의 배포 전 마이그레이션 Job과 같은 구조다 (ADR 0001).
4. 데모 데이터는 스키마 마이그레이션에 넣지 않고, `migrate` 서비스가 마이그레이션 후 멱등(idempotent) 시드 스크립트로 넣는다. `SEED_DEMO_DATA=false`로 끌 수 있다.
   - 여러 조직과 사용자가 함께 쓰는 상황(조직 내 공유, 조직 간 격리, 내부 사용자)을 바로 확인할 수 있도록 다음 데이터를 고정 UUID로 넣는다.

   | 구분 | ID | 소속 | 역할 | 확인할 수 있는 것 |
   |---|---|---|---|---|
   | 조직 A | `00000000-0000-0000-0000-000000000100` | | | |
   | 조직 B | `00000000-0000-0000-0000-000000000200` | | | |
   | 사용자 1 | `00000000-0000-0000-0000-000000000001` | 조직 A | `CUSTOMER` | 기본 데모 사용자 |
   | 사용자 2 | `00000000-0000-0000-0000-000000000002` | 조직 A | `CUSTOMER` | 같은 조직 모델 공유 |
   | 사용자 3 | `00000000-0000-0000-0000-000000000003` | 조직 B | `CUSTOMER` | 다른 조직 모델은 404 |
   | 사용자 4 | `00000000-0000-0000-0000-000000000004` | 없음 | `ML_ENGINEER` | 내부 API 호출, 고객 API는 403 |
5. 포트: 컨테이너 내부 포트는 고정하고, 호스트 노출 포트만 환경 변수와 기본값으로 둔다.
   - `compose.yaml`: API는 `${API_PORT:-8000}:8000`. DB 포트는 호스트에 노출하지 않는다.
   - 로컬 테스트용 DB 포트 노출(`${DB_PORT:-5432}:5432`)은 `compose.dev.yaml`에서만 한다.
   - `compose.override.yaml` 이름은 쓰지 않는다. `docker compose up` 시 자동으로 병합되어 기본 실행에서도 DB 포트가 노출되기 때문이다.
6. DB 연결 예산: Pod당 uvicorn 워커 1개, 커넥션 풀 `pool_size=5`, `max_overflow=5`, `pool_timeout=5`초 (환경 변수로 조정, ADR 0002).

## 기술 스택
- Python 3.12, FastAPI, Pydantic v2
- 패키지 관리: uv (`pyproject.toml` + `uv.lock` 커밋, 런타임/개발 의존성 그룹 분리)
- SQLAlchemy 2.1 (2.0 스타일 API), Alembic, psycopg 3
- jsonschema (버전 입출력 스키마의 메타스키마 검증)
- PostgreSQL 16
- pytest, httpx
- Ruff (lint, format)
- Docker 멀티스테이지 빌드, non-root 실행, healthcheck, `depends_on: condition: service_healthy`
- 베이스 이미지는 `latest` 대신 구체적인 태그로 고정한다 (예: `python:3.12-slim`, `postgres:16-alpine`).
- 런타임 이미지에는 개발 의존성(pytest, ruff)을 넣지 않는다.

## 주요 명령어
```bash
# 실행 (평가 및 배포와 동일한 경로)
docker compose up -d --build
docker compose logs -f api
docker compose down -v                    # 종료 및 볼륨 삭제

# 만료 모델 영구 삭제 (운영은 Kubernetes CronJob, ADR 0005)
docker compose exec api python -m scripts.purge_expired_models --dry-run
docker compose exec api python -m scripts.purge_expired_models

# 로컬 개발
uv sync                                   # 의존성 설치 (개발 그룹 포함)
docker compose -f compose.yaml -f compose.dev.yaml up -d db   # 테스트용 DB만 기동 (호스트 포트 노출)
uv run pytest                             # 테스트 (ADR 0004)
uv run ruff check . && uv run ruff format --check .   # 린트
uv run alembic revision --autogenerate -m "<설명>"     # 마이그레이션 생성
```

## API
### 운영 엔드포인트 (필수, compose healthcheck가 사용)
- `GET /health/live`: 프로세스 생존 확인 (DB 확인 없음)
- `GET /health/ready`: DB 연결까지 확인

### Core
| 작업 | 메서드 | 경로 | 설명 |
|---|---|---|---|
| Create | POST | `/api/v1/models` | 모델 생성 |
| Read | GET | `/api/v1/models/{model_id}` | 모델 단건 조회 (버전 목록 포함) |
| Update | PATCH | `/api/v1/models/{model_id}` | 이름, 설명 수정. 유형은 버전이 없을 때만 수정 |
| Delete | DELETE | `/api/v1/models/{model_id}` | 소프트 삭제 |

### Extended (Core 완료 후, 모두 필수 구현)
- `GET /api/v1/models`: 목록 조회 (커서(keyset) 페이지네이션, `status=active|deleted|all` 필터, 기본값 `active`)
  - `deleted`는 복원 가능한 모델을 보여주는 휴지통 화면용이다. 정의되지 않은 값은 422.
- `POST /api/v1/models/{model_id}/restore`: 복원
- `POST /api/v1/models/{model_id}/versions`: 버전 생성 (version_no 자동 증가)
- `scripts/purge_expired_models.py`: 복원 기한이 지난 모델 영구 삭제 커맨드 (API 아님, ADR 0005)

### Internal (학습 파이프라인용)
- `PATCH /internal/v1/model-versions/{version_id}/status`: 버전 상태 변경 (학습 시작, 완료, 실패, 보관)
  - 학습 파이프라인이 호출하는 내부 API다. 운영에서는 API Gateway에 노출하지 않고 클러스터 내부에서만 호출한다.
  - 내부 사용자(`ML_ENGINEER`, `ADMIN`)만 호출할 수 있다. 고객이 호출하면 403 `FORBIDDEN`. 고객이 학습 결과를 임의로 `READY`로 바꾸지 못하게 하기 위해서다.
  - OpenAPI에서는 `Internal` 태그로 고객 API와 구분한다.

### 요청자 식별
- `X-User-Id` 헤더를 FastAPI `APIKeyHeader` 보안 스키마로 등록한다. Swagger의 Authorize 버튼으로 한 번 입력하면 모든 요청에 붙는다.
- 헤더가 없거나, UUID 형식이 아니거나, 존재하지 않는 사용자면 401 `UNAUTHORIZED`.
- 고객 API(`/api/v1`)의 요청 범위는 요청자가 속한 조직이다. 조직이 없는 내부 사용자는 403 `FORBIDDEN` (ADR 0006).
- 내부 API(`/internal/v1`)는 내부 사용자만 호출할 수 있고, 조직 범위 제한이 없다. 고객은 403 `FORBIDDEN`.

### 응답 형식
- 성공 응답은 래핑하지 않고 리소스를 그대로 반환한다. 목록만 `{"items": [...], "next_cursor": "..." | null}` 형식이다.
- 에러 응답은 항상 `{"error": {"code": "MODEL_NOT_FOUND", "message": "...", "details": null}}` 형식이다. `details`에는 필드별 검증 오류 등 추가 정보를 담는다.
- 시각은 UTC ISO 8601(`2026-10-06T03:00:00Z`)로 반환한다.
- 모델 단건 응답(생성, 조회, 수정, 복원)에는 `ETag` 헤더를 붙인다 (아래 "동시 수정 방지", ADR 0007).

### 언어와 현지화
- OpenAPI의 summary, description은 한국어로 쓴다.
- 에러 `message`는 영어 한 문장으로 쓴다. 개발자가 로그와 응답을 읽기 위한 문장이며, 사용자 화면에 그대로 보여주지 않는다.
- 클라이언트는 `code`로 분기하고, 화면 문구는 클라이언트가 사용자 언어(한국어, 일본어 등)로 현지화한다. 클라이언트는 `message`를 파싱하지 않는다. 그래서 `code`는 한 번 정하면 바꾸지 않는다.

### 에러 코드
에러 코드는 `app/core/errors.py`의 카탈로그(`ERROR_CATALOG`) 한 곳에서 HTTP 상태, 발생 조건, 기본 메시지와 함께 정의하고, README에 같은 표(코드, HTTP 상태, 발생 조건)로 정리한다. 코드를 추가하거나 바꾸면 README 표도 함께 고친다 (ADR 0008).

| 코드 | HTTP | 발생 조건 |
|---|---|---|
| `UNAUTHORIZED` | 401 | `X-User-Id`가 없거나, UUID 형식이 아니거나, 존재하지 않는 사용자 |
| `FORBIDDEN` | 403 | 내부 사용자가 고객 API를 호출하거나, 고객이 내부 API를 호출 |
| `NOT_FOUND` | 404 | 정의되지 않은 경로 |
| `METHOD_NOT_ALLOWED` | 405 | 경로가 지원하지 않는 HTTP 메서드 (`Allow` 헤더 포함) |
| `MODEL_NOT_FOUND` | 404 | 모델이 없음, 삭제됨(조회/수정/삭제/버전 생성), 다른 조직 소유, 영구 삭제됨 |
| `MODEL_VERSION_NOT_FOUND` | 404 | 내부 API에서 버전이 없거나, 모델이 영구 삭제됨 (복원 기한 경과 포함) |
| `INVALID_STATUS_TRANSITION` | 409 | 허용되지 않은 버전 상태 전이 (같은 상태로의 전이 포함) |
| `PRECONDITION_FAILED` | 412 | `If-Match`의 ETag가 현재 모델 상태와 다름. 응답에 현재 `ETag` 헤더 포함 |
| `MODEL_NAME_CONFLICT` | 409 | 생성, 이름 수정, 복원 시 같은 조직의 활성 모델 이름과 중복 |
| `MODEL_VERSION_CONFLICT` | 409 | 버전 번호 unique 제약 위반 (재시도 가능) |
| `TASK_TYPE_LOCKED` | 409 | 버전이 있는 모델의 `task_type` 변경 시도 |
| `MODEL_NOT_DELETED` | 409 | 삭제되지 않은 모델 복원 시도 |
| `MODEL_PURGED` | 410 | 복원 기한(`purge_at`)이 지났지만 아직 영구 삭제 전인 모델 복원 시도 |
| `EMPTY_UPDATE` | 422 | 변경할 필드가 없는 PATCH 요청(`{}`) |
| `INVALID_PARENT_VERSION` | 422 | 부모 버전이 없거나, 다른 모델의 버전이거나, `READY`가 아님 |
| `VALIDATION_ERROR` | 422 | 요청 형식이나 값 검증 실패 (정의되지 않은 필드, 잘못된 커서, 잘못된 JSON Schema 포함) |
| `SERVICE_UNAVAILABLE` | 503 | DB 커넥션 풀 대기 시간 초과 또는 DB 연결 불가. `Retry-After` 헤더 포함 |
| `INTERNAL_ERROR` | 500 | 처리되지 않은 예외. 내부 정보는 응답에 넣지 않고 로그로만 남김 |

## 도메인 규칙
- 모델은 고객 조직이 소유한다 (ADR 0006). 같은 조직의 사용자는 조직의 모든 모델을 다룰 수 있고, 다른 조직의 모델은 404다.
  - 조직 소유 테이블을 다루는 repository 함수는 `organization_id`를 필수 인자로 받는다. 조직 조건 없는 조회 함수를 만들지 않는다.
  - `models.created_by`는 만든 사용자 기록용이며 권한 판단에 쓰지 않는다.
- 같은 조직 내 활성 모델 이름은 중복될 수 없다 (partial unique index `(organization_id, name) WHERE deleted_at IS NULL`).

### 모델 이름과 설명 (생성, 수정 공통)
- 이름은 다음 순서로 정리한 뒤 저장하고 비교한다.
  1. 유니코드 **NFC 정규화** (`unicodedata.normalize("NFC", ...)`)
  2. 앞뒤 공백 제거
  3. 길이 검사: 1–100자. 비어 있으면 422.
- NFC를 쓰는 이유: 같은 글자가 조합형(NFC)과 분해형(NFD, macOS 파일명 등)으로 다르게 들어오면 중복 검사를 통과하고 글자 수도 다르게 세어진다. NFC는 보이는 모양을 바꾸지 않고 이 차이만 없앤다.
  - 전각/반각까지 통일하는 NFKC는 쓰지 않는다. 사용자가 입력한 모양(`Ａ`, `ｶ` 등)을 바꾸기 때문이다.
- 이름은 한국어, 일본어 등 모든 언어를 허용하고, 대소문자를 구분한다.
- 설명(`description`)은 선택 항목이며 최대 2000자다.
- 삭제는 소프트 삭제: `deleted_at = now()`, `purge_at = deleted_at + MODEL_RESTORE_WINDOW_DAYS` (기본 30일).
  - 삭제 응답은 200과 `{"id", "deleted_at", "purge_at"}`이다. 클라이언트가 복원 기한을 사용자에게 알려줄 수 있게 하기 위해서다.
- 삭제된 모델은 조회, 수정 대상에서 제외된다 (404). 복원만 가능하다.
- `purge_at`이 지난 모델은 복원할 수 없다 (410 `MODEL_PURGED`). 영구 삭제된 뒤에는 404다.
- **복원 기한이 지나면 그 즉시 영구 삭제된 것으로 본다. 유예 상태는 없다.** 영구 삭제 배치가 아직 돌지 않아 행이 남아 있어도 모든 API에서 영구 삭제된 모델과 같게 다룬다 (목록에서 제외, 내부 API 404). 판단 기준은 DB 시각 `purge_at <= now()`다. 복원 요청만 410 `MODEL_PURGED`로 "영구히 없어졌음"을 알린다 (ADR 0009).
- 복원 시 같은 이름의 활성 모델이 있으면 409를 반환한다.
- `purge_at`이 지난 모델은 `scripts/purge_expired_models.py`가 배치 단위로 영구 삭제하고, `model_versions`는 FK `ON DELETE CASCADE`로 함께 삭제된다 (ADR 0005).
- 인증은 이 서비스 범위 밖이다 (API Gateway에서 처리한다고 가정). 요청자는 `X-User-Id` 헤더로 식별한다.

### 모델 수정 (PATCH)
- JSON Merge Patch(RFC 7396) 의미를 따른다. 생략한 필드는 변경하지 않고, `null`을 보내면 nullable 필드(`description`)를 비운다.
- `name`은 필수 항목이므로 `null`, 빈 문자열, 공백만 있는 값은 422다. 정리 규칙은 위 "모델 이름과 설명"을 따른다.
- 빈 요청 `{}`은 422 `EMPTY_UPDATE`다. 조용히 200으로 넘기지 않는다.
- 정의되지 않은 필드가 있으면 422 `VALIDATION_ERROR`다 (`extra="forbid"`).
- `task_type`은 모델에 버전이 하나도 없을 때만 수정할 수 있다. 버전이 있으면 409 `TASK_TYPE_LOCKED`.
  - 현재 값과 같은 `task_type`을 보내는 것은 변경이 아니므로 버전이 있어도 허용한다.
  - 버전 존재 확인은 ADR 0003과 같은 모델 행 잠금(`FOR NO KEY UPDATE`) 안에서 한다.
  - 근거: 버전이 생긴 뒤 유형이 바뀌면 서빙 중인 추론 결과 형식, 성능 지표, 재학습 계보, 학습 데이터 정답 컬럼 타입이 모델 정의와 어긋난다. 다른 유형이 필요하면 새 모델을 만든다.
- `name`, `description`은 버전 수와 관계없이 언제나 수정할 수 있다.

### 모델 버전
- 버전 번호는 모델별로 1부터 순차 증가하며, 동시 생성 시에도 중복되지 않는다 (ADR 0003).
- 새 버전의 초기 상태는 `PENDING`이다. 이전 버전이 학습 중이어도 새 버전 생성을 허용한다.
- `parent_version_id`는 같은 모델에 속하고 상태가 `READY`인 버전만 허용한다. 아니면 422 `INVALID_PARENT_VERSION`.
- `input_schema`, `output_schema`는 저장 전에 JSON Schema Draft 2020-12 메타스키마로 검증한다. 스키마 문서 자체가 올바른지 확인하는 것이며, 실패하면 422 `VALIDATION_ERROR`이고 `details`에 오류 위치를 담는다.
  - 이유: 이 스키마는 추론 게이트웨이의 요청 검증, 클라이언트 입력 화면 생성, 버전 간 비교에 쓰이는 계약이다. 버전은 불변이라 잘못 저장되면 고칠 수 없고, 오류는 추론 시점에야 드러난다. 그래서 저장 시점에 바로 실패시킨다.
  - 스키마가 실제 데이터셋 컬럼과 맞는지는 이 서비스에서 확인하지 않는다 (학습 파이프라인의 책임).
- 모델 아티팩트와 체크포인트 저장 경로는 `version_no`가 아니라 UUID로만 만든다. `version_no` 기반 경로는 덮어쓰기 위험이 있다.
  - 예: `models/{model_id}/versions/{version_id}/`, `checkpoints/{training_job_id}/`

### 버전 상태 전이 (내부 API)
- 허용하는 전이는 다음뿐이다. 그 외(같은 상태로의 전이 포함)는 409 `INVALID_STATUS_TRANSITION`.

  | 현재 | 다음 | 의미 |
  |---|---|---|
  | `PENDING` | `TRAINING` | 학습 시작 |
  | `PENDING` | `FAILED` | 자원 할당 실패 등으로 시작 전 실패 |
  | `TRAINING` | `READY` | 학습 완료. `artifact_uri` 필수 |
  | `TRAINING` | `FAILED` | 학습 실패 |
  | `READY` | `ARCHIVED` | 더 이상 쓰지 않는 버전 보관 |

- `FAILED`, `ARCHIVED`는 최종 상태다. 다시 학습하려면 새 버전을 만든다 (버전은 불변).
- `artifact_uri`는 `READY`로 전이할 때만 받는다. 다른 전이에서 보내면 422.
- 모델 행을 `FOR NO KEY UPDATE`로 잠근 뒤 버전 상태를 바꾼다 (ADR 0003). 버전 상태도 모델 단건 응답에 포함되므로 모델의 `row_version`도 올린다 (ADR 0007).
- 소프트 삭제된 모델의 버전도 상태를 바꿀 수 있다. 삭제 전에 시작한 학습이 끝나면 결과를 기록해야 복원 후 상태가 정확하다. 영구 삭제된 뒤(복원 기한 경과 포함)에는 404 `MODEL_VERSION_NOT_FOUND`.
- `artifact_uri`는 스킴이 있는 절대 URI(최대 2048자)이고, 경로에 `models/{model_id}/versions/{version_id}/`가 있어야 한다. 아니면 422 `VALIDATION_ERROR`.
  - 이유: 아티팩트 경로를 UUID로만 만드는 규칙(덮어쓰기 방지)을 API에서 보장하고, 영구 삭제 시 모델 ID로 아티팩트를 빠짐없이 찾아 파기할 수 있게 한다 (ADR 0005). 버킷과 스킴은 제한하지 않는다.

### 동시 수정 방지 (ETag, ADR 0007)
- 같은 조직의 여러 사용자가 같은 모델을 동시에 고칠 때, 다른 사람의 수정을 모르고 덮어쓰는 일(lost update)을 막는다.
- `models.row_version`(정수)은 모델 단건 응답이 바뀌는 모든 쓰기(수정, 삭제, 복원, 버전 생성, 버전 상태 변경)에서 1씩 오른다.
- 모델 단건 응답에는 `ETag: "<row_version>"`(strong ETag)을 붙인다.
- 수정(PATCH), 삭제(DELETE), 복원(POST restore)은 `If-Match` 헤더를 선택적으로 받는다.
  - 있으면: 모델 행을 잠근 뒤 현재 ETag와 비교하고, 다르면 412 `PRECONDITION_FAILED`와 현재 `ETag`를 반환한다.
  - 없으면: 비교 없이 처리한다(마지막 쓰기 우선). Swagger나 curl로 바로 호출할 수 있게 하기 위해서다.
  - `*`는 모델이 존재하면 통과한다. 약한 ETag(`W/"..."`)는 strong 비교 규칙에 따라 일치하지 않는 것으로 본다(412).

## 저장소 구조
```
model-registry-api/
├── app/
│   ├── api/
│   │   ├── v1/           # 고객 API 라우터 (models.py, model_versions.py)
│   │   ├── internal/     # 내부 API 라우터 (학습 파이프라인용, Gateway 미노출)
│   │   ├── health.py     # 운영 엔드포인트 (버전 없음)
│   │   └── deps.py       # 공통 의존성 (DB 세션, 요청자 식별)
│   ├── services/         # 유스케이스, 비즈니스 규칙, 트랜잭션 경계
│   ├── repositories/     # DB 접근 (쿼리만, 비즈니스 규칙 없음)
│   ├── db/
│   │   ├── base.py       # DeclarativeBase, 공통 컬럼 mixin
│   │   ├── session.py    # 엔진, 세션 팩토리
│   │   └── entities/     # SQLAlchemy ORM 엔티티
│   ├── schemas/          # Pydantic 요청/응답 DTO
│   ├── core/             # 설정, 예외, 예외 핸들러, 로깅, 요청 ID 미들웨어 (에러 코드는 errors.py)
│   └── main.py           # 앱 팩토리
├── migrations/           # Alembic (env.py, versions/)
├── tests/
│   ├── unit/             # DB 없이 실행하는 테스트 (서비스 로직, 미들웨어 등)
│   ├── integration/      # API + 실제 PostgreSQL 통합 테스트
│   ├── conftest.py
│   └── support.py        # 테스트 공용 헬퍼 (테스트용 설정 등)
├── scripts/
│   ├── seed.py           # 멱등 데모 데이터 시드 (migrate 서비스에서 실행)
│   └── purge_expired_models.py   # 복원 기한이 지난 모델 영구 삭제 (CronJob으로 실행)
├── docs/
│   ├── erd.md
│   └── adr/
├── .github/
│   ├── workflows/        # CI (lint, test)
│   ├── dependabot.yml    # Actions, Python 의존성 주간 업데이트 PR
│   └── pull_request_template.md
├── Dockerfile
├── compose.yaml          # 실행용 (DB 포트 미노출)
├── compose.dev.yaml      # 로컬 테스트용 DB 포트 노출 (겹쳐 쓰기)
├── .dockerignore
├── .editorconfig
├── .env.example
├── .gitattributes
├── .gitignore
├── alembic.ini
├── CLAUDE.md             # AI 코딩 도구용 프로젝트 규칙
├── CONTRIBUTING.md       # Git 작업 규칙
├── pyproject.toml        # 의존성, ruff, pytest 설정
├── uv.lock               # 의존성 잠금 파일 (커밋)
└── README.md
```
- 새 파일은 이 구조 안에 둔다. 구조를 바꿔야 하면 먼저 제안하고 승인받는다.

## 코딩 규칙
### API 설계 원칙
- 클라이언트를 믿지 않는다. 같은 규칙을 클라이언트와 API 양쪽에 둔다.
  - 프론트엔드 검증은 사용자 편의용이고, API 검증은 데이터를 지키는 최종 방어선이다.
  - API는 어떤 클라이언트(웹, Swagger, curl, 다른 서비스)가 호출하든 모든 규칙을 스스로 검증한다.
- 요청이 의미 없거나 모호하면 조용히 넘기지 않고 오류로 알린다.

### 구현 규칙
- 계층 의존 방향: `api → services → repositories → db.entities`. 역방향 의존 금지.
- 도메인 예외는 `AppError(ErrorCode.XXX)`로 표현한다. 서비스 계층은 HTTP를 모른 채 에러 코드로 실패를 알리고, 전역 핸들러가 카탈로그의 HTTP 상태와 공통 응답 형식으로 바꾼다 (ADR 0008). 에러 코드와 응답 형식은 위 "API > 응답 형식, 에러 코드"를 따른다.
- 엔드포인트의 OpenAPI `responses`는 `error_responses(ErrorCode...)`로 만든다. 낼 수 있는 에러 코드를 빠짐없이 적는다.
- 트랜잭션 경계는 service 계층의 유스케이스 단위다. 수정, 삭제, 복원, 버전 생성, 버전 상태 변경은 각각 하나의 트랜잭션으로 처리하고, 모델 행을 `FOR NO KEY UPDATE`로 잠근다 (ADR 0003).
- 시간: DB는 `timestamptz`, API 응답은 UTC ISO 8601(`2026-10-06T03:00:00Z`)로 통일한다. 서버 코드에서 naive datetime을 쓰지 않는다.
  - 근거: 저장과 API는 UTC로 두고 화면 표시만 현지 시간으로 바꾸는 것이 표준이다. 고객이 한 시간대에만 있으면 차이가 없지만, 시간대가 다르거나 서머타임이 있는 지역으로 확장하면 특정 시간대에 묶인 데이터를 변환해야 한다. 그래서 처음부터 UTC로 둔다.
- 모든 엔드포인트에 OpenAPI summary, description, 응답 모델, 에러 응답을 명시한다.
- 네이밍: 테이블과 컬럼은 snake_case, 테이블명은 복수형, API 경로는 복수 명사.
- 설정값은 하드코딩하지 않고 `pydantic-settings`로 환경 변수에서 읽는다.
- 이름 충돌 주의: 이 서비스의 도메인 "모델(ML 모델)"과 ORM/Pydantic의 "model" 용어가 겹친다.
  - ORM 엔티티 클래스는 `MLModel`, `MLModelVersion`으로 쓰고, 테이블명은 `models`, `model_versions`를 유지한다.
  - Pydantic v2는 `model_` 접두사 필드(`model_id` 등)에 경고를 내므로 해당 스키마에 `model_config = ConfigDict(protected_namespaces=())`를 지정한다.
- 의존성은 `pyproject.toml`에 버전 범위를 명시하고, 사용하지 않는 패키지는 넣지 않는다.
- 로그는 표준 `logging`으로 남기고 `print`를 쓰지 않는다.
- 요청 ID: 요청마다 ID를 정하고, 응답 헤더와 그 요청의 모든 로그에 남긴다.
  - 요청에 `X-Request-ID`가 있으면 그대로 쓴다. 없거나 형식이 맞지 않으면(영문, 숫자, `-`, `_` 외의 문자 또는 128자 초과) 새 UUID를 만든다. 로그 오염과 헤더 주입을 막기 위해서다.
  - 응답에는 항상 `X-Request-ID` 헤더를 붙인다. 에러 응답도 같다. 고객 문의 시 이 값 하나로 해당 요청의 로그를 찾는다.
  - 요청 범위 값(요청 ID, 조직 ID, 사용자 ID)은 `contextvars`로 전달해 로그 필터가 자동으로 붙인다. 함수 인자로 넘기지 않는다.
- JSON 구조화 로그: 로그 한 줄을 JSON 객체 하나로 남긴다 (`LOG_FORMAT=json` 기본값, 로컬 개발용 `text`).
  - 공통 필드: `timestamp`(UTC ISO 8601), `level`, `logger`, `message`, `request_id`, `organization_id`, `user_id`
  - 요청당 접근 로그 한 줄: `method`, `path`, `status`, `latency_ms`. uvicorn 기본 접근 로그는 중복되므로 끈다.
  - 요청, 응답 본문은 로그에 남기지 않는다. 고객 데이터와 개인정보가 섞일 수 있기 때문이다.
  - 이유: Kubernetes의 로그 수집기(Fluent Bit, Loki 등)가 필드 단위로 바로 검색, 집계할 수 있다. 예: 조직별 5xx, 응답 지연 상위 요청.

## 작업 방식
- 큰 변경 전에는 계획(변경 파일, 근거)을 먼저 제시하고 승인 후 진행한다.
- 변경 후 테스트와 린트를 실행하고 결과를 보고한다. 실패한 상태로 커밋하지 않는다.
- 통합 테스트는 실제 PostgreSQL에 대해 실행하고, 개발용 DB와 분리된 테스트 DB(`registry_test`)를 쓴다. 스키마는 Alembic으로 만들고, 테스트마다 테이블을 TRUNCATE한다 (ADR 0004).
- 중요한 설계 결정은 `docs/adr/NNNN-제목.md`에 ADR로 남긴다 (배경, 결정, 대안, 결과).
- 문서와 코드가 어긋나면 코드(마이그레이션)를 기준으로 문서를 고친다.

## Git 워크플로
브랜치, 커밋 메시지, PR 규칙은 `CONTRIBUTING.md`를 따른다.
@CONTRIBUTING.md

AI 작업 시 추가 규칙:
- 작업을 시작하기 전에 현재 브랜치를 확인하고, `main`이면 규칙에 맞는 브랜치를 새로 만든다.
- 커밋 전에 변경 파일 목록과 커밋 메시지를 보여준다.
- PR 생성(`gh pr create`)은 `.github/pull_request_template.md` 양식으로 본문을 채운다.
- push, PR 머지, 브랜치 삭제는 사용자 승인 후에만 한다.
