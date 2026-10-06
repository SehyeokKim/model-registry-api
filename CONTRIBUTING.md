# Contributing

이 문서는 `model-registry-api` 저장소의 Git 작업 규칙을 정의한다.

## 브랜치 전략

GitHub Flow를 따른다.

- `main`은 항상 배포 가능한 상태를 유지한다. 루트에서 `docker compose up -d`로 기동되고 테스트가 통과해야 한다.
- `main`에 직접 커밋하거나 push하지 않는다. 모든 변경은 브랜치와 PR을 거친다.
- 브랜치는 작업 하나당 하나를 만들고, 머지 후 삭제한다.

### 브랜치 이름

`<type>/<짧은-설명>` 형식으로, 영어 소문자와 하이픈만 쓴다 (한글 브랜치명은 도구 호환성 문제로 쓰지 않는다).

| type | 용도 | 예시 |
|---|---|---|
| `feat` | 기능 추가 | `feat/model-crud`, `feat/model-restore` |
| `fix` | 버그 수정 | `fix/duplicate-version-no` |
| `refactor` | 동작 변경 없는 구조 개선 | `refactor/error-handler` |
| `test` | 테스트 추가, 수정 | `test/model-api` |
| `docs` | 문서 | `docs/readme`, `docs/adr-soft-delete` |
| `build` | Docker, 의존성, 빌드 설정 | `build/docker-compose` |
| `ci` | CI 설정 | `ci/github-actions` |
| `chore` | 그 외 설정, 정리 | `chore/ruff-config` |

## 커밋 메시지

[Conventional Commits](https://www.conventionalcommits.org/ko/v1.0.0/)를 따른다.

```
<type>(<scope>): <subject>

<body>
```

### 언어 규칙

- **`type`과 `scope`는 영어**, **`subject`와 `body`는 한국어**로 작성한다.
- 코드 식별자, 테이블명, 라이브러리명 등 고유한 이름은 원문 그대로 쓴다. 예: `purge_at`, `Alembic`
- 한 저장소 안에서 언어를 섞지 않는다.

### 작성 규칙

- `type`: 위 브랜치 type과 같은 목록을 쓴다.
- `scope` (선택): 변경 범위. 예: `models`, `versions`, `db`, `docker`
- `subject`: 50자 이내의 한국어. "추가", "수정", "제거"처럼 명사형으로 끝내고, 끝에 마침표를 붙이지 않는다.
- `body` (선택): 무엇을, 왜 바꿨는지 한국어로 쓴다. 어떻게는 코드가 설명한다. 제목과 본문 사이는 한 줄 비운다.
- 하나의 커밋은 하나의 논리적 변경만 담는다. 테스트가 깨진 상태로 커밋하지 않는다.

예시:

```
feat(models): 모델 소프트 삭제 및 복원 기한 추가

삭제 시 deleted_at과 purge_at을 기록하고, 복원 기한이 지난 모델은
복원할 수 없도록 한다. 기한은 MODEL_RESTORE_WINDOW_DAYS로 설정한다.
```

```
fix(versions): 동시 생성 시 version_no 중복 방지
```

## Pull Request

1. 브랜치에서 작업하고 커밋한다.
2. PR을 만든다. 본문은 [PR 템플릿](.github/pull_request_template.md)을 채운다.
3. 머지 전 체크리스트를 모두 확인한다.
4. **Squash merge**로 머지하고 브랜치를 삭제한다.

### PR 제목

Squash merge 시 PR 제목이 `main`의 커밋 메시지가 되므로, PR 제목도 커밋 메시지 형식과 언어 규칙을 따른다. PR 본문도 한국어로 작성한다.

```
feat(models): 모델 CRUD API 추가
```

### 머지 전 체크리스트

- [ ] `uv run ruff check .`와 `uv run ruff format --check .` 통과
- [ ] `uv run pytest` 통과
- [ ] `docker compose down -v && docker compose up -d --build` 후 마이그레이션, 시드, API 기동 정상
- [ ] API 변경 시 OpenAPI 문서(`/docs`)에 반영됨
- [ ] 스키마 변경 시 Alembic 마이그레이션 포함, `docs/erd.md` 갱신
- [ ] 주요 설계 결정이 있으면 `docs/adr/`에 ADR 추가

## 설계 결정 기록 (ADR)

되돌리기 어렵거나 대안이 여럿인 결정은 `docs/adr/NNNN-<제목>.md`로 남긴다.
번호는 0001부터 순서대로 붙이고, 내용은 배경, 결정, 검토한 대안, 결과(트레이드오프)로 구성한다.

## 커밋하지 않는 것

- 비밀값과 실제 환경 변수 파일 (`.env`). 필요한 변수 목록은 `.env.example`에 기본값과 함께 문서화한다.
- IDE, OS 설정 파일, 로컬 실행 산출물
