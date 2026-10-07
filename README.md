# Model Registry API

대상 플랫폼은 고객이 CSV/Excel 데이터를 올리고, 고른 데이터로 GPU 학습을 요청하고, 버전 관리된 모델로 추론하는 AutoML 서비스입니다.
이 저장소는 플랫폼의 **모델 관리 API 서버**입니다. 모델(`models`)과 모델 버전(`model_versions`)의 생명주기를 관리합니다.

- 생성, 조회, 수정, 소프트 삭제, 목록, 복원, 영구 삭제
- 모델 버전 생성(번호 자동 증가)과 학습 파이프라인용 버전 상태 전이
- 고객 조직 단위의 소유와 격리, 같은 조직 사용자 사이의 동시 수정 방지

기술 스택: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.1, Alembic, PostgreSQL 16, uv, Docker Compose

## 목차
- [빠른 시작](#빠른-시작)
- [API 사용해 보기](#api-사용해-보기)
- [API 요약](#api-요약)
- [에러 코드](#에러-코드)
- [운영 작업과 설정](#운영-작업과-설정)
- [개발](#개발)
- [저장소 구조](#저장소-구조)
- [설계 고려 사항](#설계-고려-사항)
- [AI 도구 활용 및 검증 방식](#ai-도구-활용-및-검증-방식)

## 빠른 시작

Docker와 Docker Compose v2만 있으면 됩니다. `.env` 파일이나 다른 설정은 필요 없습니다.

```bash
docker compose up -d
```

이 명령 하나로 다음이 순서대로 실행됩니다.

1. `db`: PostgreSQL 16이 뜨고 healthcheck를 통과할 때까지 기다립니다.
2. `migrate`: Alembic 마이그레이션(`alembic upgrade head`)을 적용하고, 데모 조직과 사용자를 넣은 뒤 종료합니다.
3. `api`: `migrate`가 성공한 뒤에 시작하고, DB 연결까지 확인되면 healthy가 됩니다.

```bash
docker compose ps -a
```

```text
NAME                       SERVICE   STATUS
model-registry-api-1       api       Up (healthy)
model-registry-db-1        db        Up (healthy)
model-registry-migrate-1   migrate   Exited (0)
```

`migrate`가 `Exited (0)`인 것은 정상입니다. 마이그레이션을 한 번 실행하고 끝나는 일회성 작업입니다(ADR 0001).

| 주소 | 내용 |
|---|---|
| http://localhost:8000/docs | Swagger UI (OpenAPI 문서, 바로 호출 가능) |
| http://localhost:8000/redoc | ReDoc |
| http://localhost:8000/health/ready | DB 연결까지 확인하는 readiness |

종료하고 데이터까지 지우려면 다음을 실행합니다.

```bash
docker compose down -v
```

- 8000 포트를 이미 쓰고 있으면 `API_PORT=18000 docker compose up -d`처럼 바꿉니다. 컨테이너 내부 포트는 8000으로 고정입니다.
- DB 포트는 호스트에 노출하지 않습니다. 로컬 테스트용 노출은 `compose.dev.yaml`에서만 합니다([개발](#개발)).

## API 사용해 보기

인증은 API Gateway가 처리한다고 가정하고, 요청자는 `X-User-Id` 헤더로 식별합니다.
Swagger UI 오른쪽 위의 **Authorize** 버튼에 아래 사용자 ID를 한 번 넣으면 모든 요청에 붙습니다.

| 데모 사용자 | `X-User-Id` | 소속, 역할 | 확인할 수 있는 것 |
|---|---|---|---|
| 고객 A1 | `00000000-0000-0000-0000-000000000001` | 조직 A, `CUSTOMER` | 기본 사용자 |
| 고객 A2 | `00000000-0000-0000-0000-000000000002` | 조직 A, `CUSTOMER` | 같은 조직의 모델 공유 |
| 고객 B1 | `00000000-0000-0000-0000-000000000003` | 조직 B, `CUSTOMER` | 다른 조직의 모델은 404 |
| ML Engineer | `00000000-0000-0000-0000-000000000004` | 조직 없음, `ML_ENGINEER` | 내부 API 호출, 고객 API는 403 |

curl로 따라 해 보는 예시입니다(macOS, Linux 셸 기준).

```bash
API=http://localhost:8000
A1=00000000-0000-0000-0000-000000000001
ML=00000000-0000-0000-0000-000000000004

# 1. 모델 생성 (응답 헤더 ETag: "1")
curl -i -X POST $API/api/v1/models -H "X-User-Id: $A1" -H "Content-Type: application/json" \
  -d '{"name": "churn-predictor", "task_type": "CLASSIFICATION", "description": "고객 이탈 예측"}'
MODEL_ID=<응답의 id>

# 2. 버전 생성 (필드 없이 만들려면 본문으로 {}를 보낸다)
curl -X POST $API/api/v1/models/$MODEL_ID/versions -H "X-User-Id: $A1" \
  -H "Content-Type: application/json" -d '{"framework": "xgboost", "input_schema": {"type": "object"}}'
VERSION_ID=<응답의 id>

# 3. 학습 파이프라인이 상태를 바꾼다 (내부 API, ML Engineer만)
curl -X PATCH $API/internal/v1/model-versions/$VERSION_ID/status -H "X-User-Id: $ML" \
  -H "Content-Type: application/json" -d '{"status": "TRAINING"}'
curl -X PATCH $API/internal/v1/model-versions/$VERSION_ID/status -H "X-User-Id: $ML" \
  -H "Content-Type: application/json" \
  -d "{\"status\": \"READY\", \"artifact_uri\": \"s3://model-artifacts/models/$MODEL_ID/versions/$VERSION_ID/model.tar.gz\"}"

# 4. 조회, 조건부 수정 (If-Match에 조회 응답의 ETag를 그대로 넣는다)
curl -i $API/api/v1/models/$MODEL_ID -H "X-User-Id: $A1"
curl -X PATCH $API/api/v1/models/$MODEL_ID -H "X-User-Id: $A1" -H 'If-Match: "4"' \
  -H "Content-Type: application/json" -d '{"description": "v2 재학습 예정"}'

# 5. 삭제, 휴지통 목록, 복원
curl -X DELETE $API/api/v1/models/$MODEL_ID -H "X-User-Id: $A1"
curl "$API/api/v1/models?status=deleted" -H "X-User-Id: $A1"
curl -X POST $API/api/v1/models/$MODEL_ID/restore -H "X-User-Id: $A1"
```

> **Windows 사용자**: PowerShell이나 cmd에서 curl `-d`로 한국어를 보내면 콘솔 인코딩(cp949)으로 전송되어 400 `BAD_REQUEST`가 납니다. 본문을 UTF-8 파일로 저장해 `--data-binary @body.json`으로 보내거나 Swagger UI를 사용하세요.

## API 요약

### 고객 API (`/api/v1`, 요청자 조직 범위)

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/v1/models` | 모델 생성. `201`, `Location`, `ETag` |
| `GET` | `/api/v1/models` | 목록. 커서 페이지네이션, `status=active\|deleted\|all`, `limit` 1–100 |
| `GET` | `/api/v1/models/{model_id}` | 단건 조회 (버전 목록 포함). `ETag` |
| `PATCH` | `/api/v1/models/{model_id}` | 수정 (JSON Merge Patch). `If-Match` 선택 |
| `DELETE` | `/api/v1/models/{model_id}` | 소프트 삭제. 응답에 복원 기한 `purge_at`과 복원에 쓸 `ETag`. `If-Match` 선택 |
| `POST` | `/api/v1/models/{model_id}/restore` | 복원 기한 전의 삭제된 모델 복원. `If-Match` 선택 |
| `POST` | `/api/v1/models/{model_id}/versions` | 버전 생성 (`PENDING`, 번호 자동 증가) |

### 내부 API (`/internal/v1`, 학습 파이프라인용)

| 메서드 | 경로 | 설명 |
|---|---|---|
| `PATCH` | `/internal/v1/model-versions/{version_id}/status` | 버전 상태 전이. 내부 사용자(`ML_ENGINEER`, `ADMIN`)만 호출 |

운영에서는 API Gateway에 노출하지 않고 클러스터 내부에서만 호출합니다. 허용하는 전이는 다음뿐입니다.

```text
PENDING ──► TRAINING ──► READY ──► ARCHIVED
   │            │
   └────────────┴──► FAILED        (FAILED, ARCHIVED는 최종 상태)
```

### 운영 엔드포인트

| 경로 | 설명 |
|---|---|
| `GET /health/live` | 프로세스 생존 (liveness) |
| `GET /health/ready` | DB 연결까지 확인 (readiness, compose healthcheck가 사용) |

### 공통 규칙
- **응답 형식**: 성공 응답은 리소스를 그대로 돌려줍니다. 목록만 `{"items": [...], "next_cursor": "..." | null}`입니다.
- **에러 형식**: 항상 `{"error": {"code", "message", "details"}}`입니다. 클라이언트는 `code`로 분기하고 화면 문구는 직접 현지화합니다. `message`는 개발자용 영어 문장입니다.
- **시각**: 모두 UTC ISO 8601(`2026-10-06T03:00:00Z`)입니다.
- **요청 ID**: 모든 응답에 `X-Request-ID` 헤더가 붙습니다. 요청에 올바른 형식의 값이 있으면 그대로 쓰고, 이 값으로 해당 요청의 로그를 찾을 수 있습니다.
- **동시 수정 방지**: 모델 단건 응답과 삭제 응답의 `ETag`를 수정, 삭제, 복원 요청의 `If-Match`에 넣으면, 그사이 다른 사람이 바꿨을 때 412로 거부됩니다. `If-Match`를 생략하면 비교 없이 처리합니다.
- **모델 이름**: NFC 정규화와 앞뒤 공백 제거 후 1–100자이며, 같은 조직의 활성 모델과 겹칠 수 없습니다.

## 에러 코드

에러 코드는 `app/core/errors.py`의 `ERROR_CATALOG` 한 곳에서 정의합니다(ADR 0008). 아래 표는 카탈로그와 같아야 하며, 테스트(`tests/unit/test_readme.py`)가 일치 여부를 확인합니다.

| 코드 | HTTP | 발생 조건 |
|---|---|---|
| `BAD_REQUEST` | 400 | 요청 본문을 해석할 수 없음 (UTF-8이 아닌 인코딩 등) |
| `UNAUTHORIZED` | 401 | `X-User-Id`가 없거나, UUID 형식이 아니거나, 존재하지 않는 사용자 |
| `FORBIDDEN` | 403 | 조직에 속하지 않은 내부 사용자가 고객 API를 호출하거나, 고객이 내부 API를 호출 |
| `NOT_FOUND` | 404 | 정의되지 않은 경로 |
| `MODEL_NOT_FOUND` | 404 | 모델이 없거나, 삭제되었거나(복원 제외), 다른 조직 소유이거나, 영구 삭제됨 (존재 여부를 드러내지 않음) |
| `MODEL_VERSION_NOT_FOUND` | 404 | 내부 API에서 버전이 없거나, 소속 모델이 영구 삭제됨 (복원 기한 경과 포함) |
| `METHOD_NOT_ALLOWED` | 405 | 경로가 지원하지 않는 HTTP 메서드 |
| `MODEL_NAME_CONFLICT` | 409 | 같은 조직에 같은 이름의 활성 모델이 있음 |
| `TASK_TYPE_LOCKED` | 409 | 버전이 있는 모델의 `task_type` 변경 시도 |
| `MODEL_NOT_DELETED` | 409 | 삭제되지 않은 모델의 복원 시도 |
| `MODEL_VERSION_CONFLICT` | 409 | 버전 번호가 동시에 생성된 다른 버전과 겹침 (다시 시도하면 된다) |
| `INVALID_STATUS_TRANSITION` | 409 | 허용되지 않은 버전 상태 전이 (같은 상태로의 전이 포함) |
| `MODEL_PURGED` | 410 | 복원 기한(`purge_at`)이 지나 영구 삭제된 것으로 보는 모델의 복원 시도 |
| `PRECONDITION_FAILED` | 412 | `If-Match`의 ETag가 현재 모델과 다름 (응답에 현재 `ETag` 헤더 포함) |
| `INVALID_PARENT_VERSION` | 422 | 부모 버전이 없거나, 다른 모델의 버전이거나, `READY` 상태가 아님 |
| `EMPTY_UPDATE` | 422 | 변경할 필드가 없는 수정 요청(`{}`) |
| `VALIDATION_ERROR` | 422 | 요청 형식이나 값 검증 실패 (`details`에 필드별 오류) |
| `INTERNAL_ERROR` | 500 | 처리되지 않은 서버 오류 |
| `SERVICE_UNAVAILABLE` | 503 | DB 커넥션 풀 대기 시간 초과 또는 DB 접속 실패 (`Retry-After` 헤더 포함) |

## 운영 작업과 설정

### 복원 기한이 지난 모델 영구 삭제

삭제된 모델은 `purge_at`(삭제 후 기본 30일)까지 복원할 수 있고, 기한이 지나면 영구 삭제된 것으로 봅니다. 실제 행 삭제는 아래 커맨드가 배치 단위로 합니다(ADR 0005). 운영에서는 Kubernetes CronJob으로 매일 실행하고, compose에는 스케줄러가 없으므로 직접 실행합니다.

```bash
docker compose exec api python -m scripts.purge_expired_models --dry-run   # 대상 건수만 확인
docker compose exec api python -m scripts.purge_expired_models
```

- 배치마다 별도 트랜잭션으로 지우고, 복원 중이라 잠긴 행은 `SKIP LOCKED`로 건너뛰어 다음 실행에서 처리합니다.
- 모델 버전은 FK `ON DELETE CASCADE`로 함께 지워집니다.
- 배치마다 지운 모델 ID와 소유 조직을 JSON 로그로 남깁니다. 고객 데이터 파기 기록이자 오브젝트 스토리지 아티팩트 정리의 입력입니다.

### 환경 변수

모든 값에 기본값이 있어 설정 없이 동작합니다. 전체 목록과 설명은 [`.env.example`](.env.example)에 있습니다. 값을 바꾸려면 `.env.example`을 `.env`로 복사해 고칩니다(`.env`는 커밋하지 않습니다).

| 변수 | 기본값 | 설명 |
|---|---|---|
| `API_PORT` | `8000` | 호스트에 노출할 API 포트 |
| `LOG_LEVEL`, `LOG_FORMAT` | `INFO`, `json` | 로그 레벨과 형식(`json`은 로그 수집기용, `text`는 로컬 개발용) |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT` | `5`, `5`, `5` | 컨테이너당 DB 연결 예산(ADR 0002) |
| `MODEL_RESTORE_WINDOW_DAYS` | `30` | 삭제 후 복원할 수 있는 기간(일) |
| `PURGE_BATCH_SIZE` | `500` | 영구 삭제 한 트랜잭션의 최대 모델 수 |
| `SEED_DEMO_DATA` | `true` | 마이그레이션 후 데모 데이터를 넣을지 여부 (운영은 `false`) |

### 로그

로그는 한 줄에 JSON 객체 하나로 표준 출력에 남깁니다. 공통 필드는 `timestamp`, `level`, `logger`, `message`, `request_id`, `organization_id`, `user_id`이고, 요청마다 접근 로그 한 줄(`method`, `path`, `status`, `latency_ms`)을 남깁니다. 헬스 체크(`/health/*`)의 접근 로그는 DEBUG로 남깁니다. 요청과 응답 본문은 고객 데이터가 섞일 수 있어 남기지 않고, DB 오류 메시지에도 SQL 파라미터를 넣지 않습니다.

```bash
docker compose logs -f api
```

### 컨테이너 이미지

- `Dockerfile`은 멀티스테이지 빌드입니다. builder에서 uv로 런타임 의존성만 담은 가상환경을 만들고, runtime에는 가상환경과 실행 파일만 복사합니다.
- 베이스 이미지는 `python:3.12-slim`, `postgres:16-alpine`처럼 태그를 고정합니다. 런타임 이미지에는 개발 도구(pytest, ruff)가 없습니다.
- 이미지 크기는 약 277MB입니다. slim 베이스가 약 195MB, 런타임 의존성이 약 80MB(SQLAlchemy, psycopg와 libpq, jsonschema 등)입니다.
- non-root 사용자(UID 10001)로 실행하고, 코드 파일은 root 소유라 실행 사용자가 바꿀 수 없습니다.
- API와 `migrate`는 같은 이미지를 쓰고 실행 명령만 다릅니다.
- main에 머지되어 CI가 통과한 커밋은 `ghcr.io/sehyeokkim/model-registry-api:<커밋 SHA>`로 게시됩니다(`latest`처럼 덮어쓰이는 태그는 두지 않습니다).

### Kubernetes 참조 매니페스트

운영 환경에서의 실행 방식을 [`deploy/k8s/`](deploy/k8s/)에 참조 매니페스트로 두었습니다. 클러스터 배포 자체는 이 저장소의 범위 밖이며, 적용 순서와 Secret 준비는 [deploy/k8s/README.md](deploy/k8s/README.md)에 있습니다.

| compose | Kubernetes | 근거 |
|---|---|---|
| `migrate` 서비스 | Argo CD `PreSync` hook Job (성공해야 롤아웃 진행) | ADR 0001 |
| `api` 서비스, healthcheck | Deployment (liveness, readiness, startup probe), Service | ADR 0002 |
| 수동 `purge_expired_models` 실행 | CronJob (매일 03:00 KST, `concurrencyPolicy: Forbid`) | ADR 0005 |
| 앱 환경 변수(`DATABASE_URL`, `LOG_*`, `DB_POOL_*` 등) | ConfigMap, Secret (마이그레이션 계정과 API 계정 분리). `API_PORT`는 compose 전용이며 Kubernetes에서는 Service가 포트를 맡음 | ADR 0001 |
| | HPA 2–8개 (DB 연결 예산 `8 × 10 + 15 ≤ 100`) | ADR 0002 |

- 모든 워크로드가 non-root, 읽기 전용 루트 파일 시스템, 권한 제거(`capabilities: drop ALL`)로 실행됩니다.
- CI가 kubeconform으로 스키마를 검증하고(문법만, 배포는 하지 않음), 테스트가 매니페스트와 앱이 어긋나지 않는지 확인합니다. 확인 항목은 환경 변수 이름, probe 경로, 실행 사용자, 연결 예산입니다.

## 개발

[uv](https://docs.astral.sh/uv/)와 Docker가 필요합니다.

```bash
uv sync                                                        # 의존성 설치 (개발 그룹 포함)
docker compose -f compose.yaml -f compose.dev.yaml up -d db    # 테스트용 DB만 기동 (호스트 포트 노출)
uv run pytest                                                  # 단위 + 통합 테스트
uv run ruff check . && uv run ruff format --check .            # 린트
uv run alembic revision --autogenerate -m "<설명>"              # 마이그레이션 생성
```

- 통합 테스트는 mock이 아니라 실제 PostgreSQL에서 실행합니다. 개발 DB와 분리된 `registry_test` DB를 자동으로 만들고, 스키마는 Alembic 마이그레이션으로 만들며, 테스트마다 테이블을 비웁니다(ADR 0004).
- 5432 포트를 이미 쓰고 있으면 `DB_PORT=55432`를 DB 기동과 pytest 양쪽에 똑같이 줍니다.
- 기본 실행(`docker compose up -d`)은 DB 포트를 노출하지 않으므로, 그 상태에서는 통합 테스트가 DB에 접속하지 못합니다.

### CI/CD (GitHub Actions)

| 워크플로 | 시점 | 내용 |
|---|---|---|
| CI / Lint | PR, main | Ruff 린트와 포맷 검사, Hadolint(Dockerfile), kubeconform(Kubernetes 매니페스트) |
| CI / Test | PR, main | PostgreSQL 서비스 컨테이너로 pytest 전체 |
| CI / Compose | PR, main | `docker compose up -d --build`로 실제 기동 후 마이그레이션, 시드, 주요 API, 내부 API, ETag, 영구 삭제 커맨드, 재기동 멱등성 확인 |
| Publish image | main CI 성공 후 | GHCR에 커밋 SHA 태그로 이미지 게시 |
| Dependabot | 매주 | Actions, Python 의존성 업데이트 PR |

브랜치, 커밋, PR 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)에 있습니다. GitHub Flow와 Squash merge, Conventional Commits를 따릅니다.

## 저장소 구조

```text
app/
├── api/
│   ├── v1/            고객 API 라우터 (models, model_versions)
│   ├── internal/      내부 API 라우터 (학습 파이프라인용)
│   ├── health.py      운영 엔드포인트
│   └── deps.py        DB 세션, 요청자 식별 의존성
├── services/          유스케이스, 비즈니스 규칙, 트랜잭션 경계
├── repositories/      DB 접근 (쿼리만)
├── db/                엔진, 세션, ORM 엔티티
├── schemas/           Pydantic 요청/응답 DTO
├── core/              설정, 에러 카탈로그, 예외 핸들러, 로깅, 요청 ID, ETag
└── main.py            앱 팩토리
migrations/            Alembic 마이그레이션
scripts/               데모 데이터 시드, 영구 삭제 커맨드
tests/                 unit (DB 없음), integration (실제 PostgreSQL)
deploy/k8s/            Kubernetes 참조 매니페스트
docs/                  ERD, ADR
```

계층 의존 방향은 `api → services → repositories → db.entities`이며 역방향 의존은 없습니다. 서비스 계층은 HTTP를 모르고 에러 코드(`AppError(ErrorCode.X)`)로 실패를 알리며, 전역 핸들러가 HTTP 응답으로 바꿉니다.

## 설계 고려 사항

플랫폼 전체 데이터 모델은 [docs/erd.md](docs/erd.md)에 있고, 이 저장소는 그중 `organizations`, `users`, `models`, `model_versions`를 구현합니다. `organizations.analytics_consent`(고객 데이터의 내부 분석 동의 여부)는 플랫폼의 분석 환경이 쓰는 컬럼이며, 이 서비스의 API는 아직 읽거나 쓰지 않습니다. 주요 결정의 배경, 대안, 트레이드오프는 [docs/adr/](docs/adr/)에 ADR로 남겼습니다.

### 도메인과 데이터 모델
- **조직 단위 소유 (ADR 0006)**: 모델은 사용자 개인이 아니라 고객 조직이 소유합니다. 담당자가 바뀌어도 모델이 남고, 같은 조직 사용자끼리 공유하며, 다른 조직의 모델은 존재 여부도 드러내지 않도록 404로 응답합니다. 조직 소유 테이블의 repository 함수는 `organization_id`를 필수 인자로 받아 조직 조건 누락을 구조적으로 막습니다.
- **이름표 + 불변 버전**: 모델은 이름표이고, 학습 결과물은 불변인 버전으로 쌓입니다. 재학습의 기준(`parent_version_id`)은 같은 모델의 `READY` 버전만 허용해 계보가 끊기지 않게 합니다.
- **입출력 스키마 검증**: 버전의 `input_schema`, `output_schema`는 추론 게이트웨이와 클라이언트가 쓰는 계약입니다. 버전은 고칠 수 없으므로 저장 시점에 JSON Schema Draft 2020-12 메타스키마와 크기 상한(64 KiB)으로 검증합니다.
- **유형 잠금**: 버전이 생긴 뒤에는 `task_type`을 바꿀 수 없습니다. 추론 결과 형식, 성능 지표, 재학습 계보가 모델 정의와 어긋나기 때문입니다.
- **아티팩트 경로 규칙**: `READY` 전이의 `artifact_uri`는 경로의 첫 `models` 세그먼트 바로 뒤가 `{model_id}/versions/{version_id}/`여야 하고, `.`, `..` 세그먼트는 허용하지 않습니다. 다른 버전의 아티팩트를 덮어쓰지 않고, 영구 삭제 때 모델 ID로 아티팩트를 빠짐없이 찾을 수 있게 합니다.
- **DB 제약을 최종 방어선으로**: 활성 이름 중복(partial unique index), 이름 정규화(NFC CHECK), 소프트 삭제 컬럼 쌍, 같은 모델 안의 부모 버전(복합 FK), `READY` 버전의 아티팩트 필수를 DB 제약으로도 보장합니다.

### 트랜잭션과 동시성
- **유스케이스 하나가 트랜잭션 하나**입니다. 서비스 계층이 트랜잭션 경계를 명시하고(`autobegin=False`), 실패하면 전체를 롤백합니다. 예를 들어 이름 충돌로 실패한 수정은 함께 보낸 다른 필드도 바꾸지 않습니다.
- **모델 행 잠금 (ADR 0003)**: 수정, 삭제, 복원, 버전 생성, 버전 상태 변경은 모델 행을 `FOR NO KEY UPDATE`로 잠가 직렬화합니다. 격리 수준은 READ COMMITTED를 유지하면서 버전 번호가 빈틈과 중복 없이 증가하고(동시 10건 → 1–10), 동시에 들어온 상태 전이 중 하나만 반영됩니다. FK 참조용 `KEY SHARE` 잠금과 충돌하지 않는 모드를 골랐습니다.
- **중복은 미리 조회하지 않고 제약으로 판단**: 같은 이름 동시 생성이나 동시 복원은 unique index 위반을 도메인 에러로 바꿔 정확히 하나만 성공합니다.
- **낙관적 동시성 (ADR 0007)**: 행 잠금은 "낡은 화면을 보고 저장하는" 문제를 막지 못하므로, `row_version` 기반 strong ETag와 `If-Match`로 lost update를 막습니다. 모델 없음(404), 복원 대상 아님(409, 410)은 `If-Match`보다 먼저 판단하고, 유형 잠금과 이름 충돌은 `If-Match` 비교 뒤에 판단해 그사이 바뀐 사실을 412로 먼저 알립니다.

### 성능과 확장
- **keyset 페이지네이션**: 목록은 `(created_at, id)` 커서로 넘기고 `(organization_id, created_at, id)` 인덱스를 역방향으로 읽어, 페이지 위치와 관계없이 필요한 행만 읽습니다.
- **N+1 방지**: 목록의 버전 수와 최신 버전 번호는 `LATERAL` 집계로 페이지에 들어간 모델만 계산합니다. 모델 2만 건, 버전 6만 건에서 `EXPLAIN ANALYZE`로 21행만 읽는 것을 확인했고(0.5ms), 쿼리 수가 모델 수와 무관한지 테스트로 고정했습니다. ORM 관계는 지연 로딩을 금지(`lazy="raise"`)해 실수로 생긴 N+1이 바로 드러나게 했습니다.
- **큰 컬럼 지연 로딩**: 버전 스키마(JSONB)는 모델 조회 응답에 쓰지 않으므로 기본으로 읽지 않습니다. 스키마 문서는 64 KiB, 중첩 깊이 32로 제한합니다.
- **DB 연결 예산 (ADR 0002)**: 컨테이너당 워커 1개와 커넥션 최대 10개로 고정하고, 확장은 Pod 수로 합니다. 풀 대기가 5초를 넘으면 503과 `Retry-After`로 빠르게 실패합니다. `HPA 최대 Pod 수 × Pod당 연결 수 + 여유 ≤ max_connections` 공식으로 PgBouncer 도입 시점을 정해 두었습니다.

### 데이터 수명주기 (ADR 0005, 0009)
- 삭제는 소프트 삭제이고, 응답에 복원 기한(`purge_at`)을 담아 클라이언트가 미리 알릴 수 있게 합니다. 휴지통 목록(`status=deleted`)에는 복원할 수 있는 모델만 보입니다.
- 복원 기한이 지나면 그 즉시 영구 삭제된 것으로 봅니다. 배치가 아직 돌지 않아 행이 남아 있어도 목록에서 빠지고, 내부 API는 404, 복원은 410입니다. 모든 판단은 DB 시각 `purge_at <= now()` 하나를 기준으로 합니다.
- 기한이 지난 모델은 배치로 실제 삭제합니다. 고객 데이터 파기 의무와 개인정보 규정, 아티팩트 저장 비용 때문입니다.

### 컨테이너와 실행 환경 (ADR 0001)
- 마이그레이션은 API와 분리된 일회성 `migrate` 서비스가 실행하고, API는 그 서비스가 성공한 뒤에만 시작합니다(`service_completed_successfully`). API 레플리카가 여러 개일 때 각자 마이그레이션을 돌리는 경쟁을 없애는 구조로, Kubernetes의 배포 전 마이그레이션 Job과 같습니다.
- 데모 데이터는 스키마 마이그레이션과 분리된 멱등 시드 스크립트로 넣고, 운영에서는 끕니다.
- 생존 확인(`/health/live`)과 준비 확인(`/health/ready`, DB 연결 포함)을 나눴습니다. compose의 api healthcheck는 준비 확인을 쓰고, Kubernetes에서는 각각 liveness probe와 readiness probe가 씁니다. DB healthcheck는 첫 초기화 중의 임시 서버를 준비됨으로 오판하지 않도록 TCP로 확인합니다.
- 같은 구조를 Kubernetes 참조 매니페스트로 옮겨, ADR의 "운영에서는 이렇게 한다"를 실행 가능한 형태로 남겼습니다([운영 작업과 설정](#kubernetes-참조-매니페스트)).

### API 문서화와 에러 처리 (ADR 0008)
- 모든 엔드포인트에 한국어 summary와 description, 응답 모델, 낼 수 있는 에러 코드를 OpenAPI에 명시했습니다. 같은 HTTP 상태의 여러 에러 코드는 예시로 나눠 보여줍니다.
- 에러 코드는 카탈로그 한 곳에서 HTTP 상태, 발생 조건, 기본 메시지와 함께 정의하고, OpenAPI와 이 README의 표가 모두 카탈로그를 기준으로 합니다.
- 처리되지 않은 예외는 내부 정보 없이 500과 요청 ID만 돌려주고, 상세는 로그로만 남깁니다.

### ADR 목록

| 번호 | 결정 |
|---|---|
| [0001](docs/adr/0001-separate-migrate-service.md) | 마이그레이션을 별도 일회성 서비스로 분리 |
| [0002](docs/adr/0002-sync-sqlalchemy.md) | 동기 SQLAlchemy와 Pod당 DB 연결 예산 |
| [0003](docs/adr/0003-version-number-concurrency.md) | 모델 버전 번호 동시성 제어 |
| [0004](docs/adr/0004-test-database-strategy.md) | 실제 PostgreSQL 기반 테스트 전략 |
| [0005](docs/adr/0005-purge-expired-models.md) | 복원 기한이 지난 모델 영구 삭제 |
| [0006](docs/adr/0006-organization-ownership.md) | 모델 소유 단위를 고객 조직으로 |
| [0007](docs/adr/0007-optimistic-concurrency-etag.md) | ETag와 If-Match로 모델 동시 수정 방지 |
| [0008](docs/adr/0008-error-response-format.md) | 에러 코드 카탈로그와 공통 에러 응답 |
| [0009](docs/adr/0009-soft-delete-and-restore-window.md) | 모델 소프트 삭제와 복원 기한 |

## AI 도구 활용 및 검증 방식

이 저장소는 구현 속도를 높이고 부족한 도메인 지식을 빠르게 익히기 위해 Anthropic의 AI 코딩 도구 **Claude Code**를 활용해 만들었습니다. 요구사항 해석, 설계 결정, 코드 리뷰와 머지, 최종 확인은 사람이 하고, 조사와 구현, 테스트 작성, 검증 실행은 Claude Code가 했습니다.

### 진행 방식
- **규칙을 먼저 문서로 고정**: 작업 전에 [CLAUDE.md](CLAUDE.md)(프로젝트 규칙), [CONTRIBUTING.md](CONTRIBUTING.md)(Git 규칙), 초기 ADR을 작성하고, AI가 매 작업마다 이 규칙을 따르게 했습니다.
- **PR 단위 진행**: 기능마다 브랜치와 PR을 만들고 "계획 제시 → 승인 → 구현 → 검증 → 커밋 → CI 통과 → 머지 승인" 순서로 진행했습니다. 머지는 항상 사람이 승인했습니다.
- **결정은 사람이**: API 동작이나 비즈니스 규칙을 바꾸는 사항, 되돌리기 어려운 사항은 AI가 선택지와 트레이드오프를 제시하고 사람이 결정했습니다. 예를 들면 다음과 같습니다.
  - 복원 기한이 지난 모델은 즉시 영구 삭제된 것으로 본다(목록 제외, 복원 410, 내부 API 404)
  - 버전 스키마 크기 상한 64 KiB와 큰 컬럼 지연 로딩
  - 형식이 잘못된 `If-Match`는 불일치(412)와 구분해 422로 응답
  - `artifact_uri` 경로 규칙 검증
- **AI가 발견한 문제도 사람이 판단**: 구현 중에 AI가 찾아낸 계획 밖의 문제(예: 목록 집계 쿼리가 조직 전체 버전을 읽는 문제, 모델 조회가 쓰지 않는 스키마 문서까지 읽는 문제)는 근거와 함께 보고하고, 동작이 바뀌는 경우에는 승인을 받은 뒤 반영했습니다.

### 검증 방법
AI가 작성한 코드를 그대로 믿지 않고, 다음 방법으로 동작을 확인했습니다.
- **실제 DB 테스트**: 단위 테스트와 실제 PostgreSQL 통합 테스트를 함께 둡니다. 정상 경로, 에러 코드, 조직 격리, 롤백 범위를 모두 검증합니다.
- **동시성 테스트**: 같은 이름 동시 생성과 동시 복원, 버전 번호 동시 생성, 동시 상태 전이, 잠긴 행 건너뛰기를 여러 스레드로 실제로 경합시켜 확인합니다.
- **변이 확인**: 지연 로딩이나 `SKIP LOCKED` 같은 핵심 장치를 일부러 빼고 실행해, 해당 테스트가 실제로 실패하는지 확인했습니다. 이 과정에서 실패 시 테스트가 멈춰 버리는 구조를 발견해 고쳤습니다.
- **실행 계획 확인**: 대량 데이터를 넣고 `EXPLAIN ANALYZE`로 인덱스 사용과 읽는 행 수를 확인했습니다.
- **실제 기동 확인**: 매 PR마다 `docker compose up -d --build`로 띄워 API를 직접 호출했고, 같은 흐름을 CI의 Compose job으로 자동화했습니다.
- **커밋 단위 검증**: 여러 커밋으로 나눈 PR은 중간 커밋을 별도 worktree에 체크아웃해 각각 린트와 테스트가 통과하는지 확인했습니다.
- **문서와 코드 일치**: README의 에러 코드 표가 카탈로그와 같은지 테스트로 확인합니다.
- **독립 검토**: 범위를 넓히는 결정(예: Kubernetes 매니페스트 추가)은 별도 AI 에이전트에게 반대 입장의 검토를 맡기고, 그 근거를 ADR 원문과 대조해 확인한 뒤 진행했습니다. 그 검토에서 나온 지적(마이그레이션 Job의 실행 순서 보장, 계정 분리, 매니페스트와 앱의 불일치 위험)을 매니페스트와 테스트에 반영했습니다.
- **클러스터 없이 확인할 수 있는 것은 확인**: 매니페스트의 보안 설정(읽기 전용 루트 파일 시스템, non-root, 권한 제거)은 같은 조건의 `docker run`으로 실제 이미지가 동작하는지 확인했습니다.
