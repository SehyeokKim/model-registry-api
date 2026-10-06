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
   - 이유: API 레플리카가 여러 개일 때 각자 마이그레이션을 돌리면 경쟁 상태가 생긴다. Kubernetes의 마이그레이션 Job / initContainer 패턴과 같은 구조다.
4. 데모 데이터(사용자 1명)는 스키마 마이그레이션에 넣지 않고, `migrate` 서비스가 마이그레이션 후 멱등(idempotent) 시드 스크립트로 넣는다. `SEED_DEMO_DATA=false`로 끌 수 있다.

## 기술 스택
- Python 3.12, FastAPI, Pydantic v2
- 패키지 관리: uv (`pyproject.toml` + `uv.lock` 커밋, 런타임/개발 의존성 그룹 분리)
- SQLAlchemy 2.0, Alembic
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

# 로컬 개발
uv sync                                   # 의존성 설치 (개발 그룹 포함)
docker compose up -d db                   # 테스트용 DB만 기동
uv run pytest                             # 테스트
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
| Update | PATCH | `/api/v1/models/{model_id}` | 이름, 설명 수정 |
| Delete | DELETE | `/api/v1/models/{model_id}` | 소프트 삭제 |

### Extended (Core 완료 후)
- `GET /api/v1/models`: 목록 조회 (커서 또는 오프셋 페이지네이션, 삭제 포함 여부 필터)
- `POST /api/v1/models/{model_id}/restore`: 복원
- `POST /api/v1/models/{model_id}/versions`: 버전 생성 (version_no 자동 증가)

## 도메인 규칙
- 같은 소유자 내 활성 모델 이름은 중복될 수 없다 (partial unique index `WHERE deleted_at IS NULL`).
- 삭제는 소프트 삭제: `deleted_at = now()`, `purge_at = deleted_at + MODEL_RESTORE_WINDOW_DAYS` (기본 30일).
- 삭제된 모델은 조회, 수정 대상에서 제외된다 (404). 복원만 가능하다.
- `purge_at`이 지난 모델은 복원할 수 없다 (410).
- 복원 시 같은 이름의 활성 모델이 있으면 409를 반환한다.
- 버전 번호는 모델별로 1부터 순차 증가하며, 동시 생성 시에도 중복되지 않는다.
- 인증은 이 서비스 범위 밖이다 (API Gateway에서 처리한다고 가정). 요청자는 `X-User-Id` 헤더로 식별하며, 헤더가 없거나 존재하지 않는 사용자면 401을 반환한다.

## 저장소 구조
```
model-registry-api/
├── app/
│   ├── api/
│   │   ├── v1/           # 버전별 라우터 (models.py, model_versions.py)
│   │   ├── health.py     # 운영 엔드포인트 (버전 없음)
│   │   └── deps.py       # 공통 의존성 (DB 세션, 요청자 식별)
│   ├── services/         # 유스케이스, 비즈니스 규칙, 트랜잭션 경계
│   ├── repositories/     # DB 접근 (쿼리만, 비즈니스 규칙 없음)
│   ├── db/
│   │   ├── base.py       # DeclarativeBase, 공통 컬럼 mixin
│   │   ├── session.py    # 엔진, 세션 팩토리
│   │   └── entities/     # SQLAlchemy ORM 엔티티
│   ├── schemas/          # Pydantic 요청/응답 DTO
│   ├── core/             # 설정, 예외, 예외 핸들러, 로깅
│   └── main.py           # 앱 팩토리
├── migrations/           # Alembic (env.py, versions/)
├── tests/
│   ├── unit/             # 서비스 로직 단위 테스트
│   ├── integration/      # API + 실제 PostgreSQL 통합 테스트
│   └── conftest.py
├── scripts/
│   └── seed.py           # 멱등 데모 데이터 시드 (migrate 서비스에서 실행)
├── docs/
│   ├── erd.md
│   └── adr/
├── .github/
│   ├── workflows/        # CI (lint, test)
│   └── pull_request_template.md
├── Dockerfile
├── compose.yaml
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
- 계층 의존 방향: `api → services → repositories → db.entities`. 역방향 의존 금지.
- 도메인 예외(`NotFound`, `Conflict`, `Gone` 등)를 정의하고 전역 핸들러에서 HTTP 상태 코드로 매핑한다.
- 에러 응답 형식을 통일한다: `{"error": {"code": "MODEL_NOT_FOUND", "message": "...", "details": null}}`
- 트랜잭션 경계는 service 계층의 유스케이스 단위다. 삭제, 복원, 버전 생성은 각각 하나의 트랜잭션으로 처리한다.
- 모든 엔드포인트에 OpenAPI summary, description, 응답 모델, 에러 응답을 명시한다.
- 네이밍: 테이블과 컬럼은 snake_case, 테이블명은 복수형, API 경로는 복수 명사.
- 설정값은 하드코딩하지 않고 `pydantic-settings`로 환경 변수에서 읽는다.
- 이름 충돌 주의: 이 서비스의 도메인 "모델(ML 모델)"과 ORM/Pydantic의 "model" 용어가 겹친다.
  - ORM 엔티티 클래스는 `MLModel`, `MLModelVersion`으로 쓰고, 테이블명은 `models`, `model_versions`를 유지한다.
  - Pydantic v2는 `model_` 접두사 필드(`model_id` 등)에 경고를 내므로 해당 스키마에 `model_config = ConfigDict(protected_namespaces=())`를 지정한다.
- 의존성은 `pyproject.toml`에 버전 범위를 명시하고, 사용하지 않는 패키지는 넣지 않는다.
- 로그는 표준 `logging`으로 남기고 `print`를 쓰지 않는다.

## 작업 방식
- 큰 변경 전에는 계획(변경 파일, 근거)을 먼저 제시하고 승인 후 진행한다.
- 변경 후 테스트와 린트를 실행하고 결과를 보고한다. 실패한 상태로 커밋하지 않는다.
- 통합 테스트는 실제 PostgreSQL에 대해 실행하고, 개발용 DB와 분리된 테스트 DB를 쓴다 (테스트마다 트랜잭션 롤백 또는 테이블 초기화).
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
