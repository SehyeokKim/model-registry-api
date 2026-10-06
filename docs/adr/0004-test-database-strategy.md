# 0004. 통합 테스트 DB 전략

- 상태: 채택됨
- 날짜: 2026-10-06

## 배경

- 이 서비스의 핵심 규칙은 PostgreSQL 기능에 기대고 있다.
  - partial unique index(활성 모델 이름 중복 방지), CHECK 제약, JSONB, `timestamptz`
  - `SELECT ... FOR NO KEY UPDATE` 행 잠금 ([0003](0003-version-number-concurrency.md))
- 이 동작들은 SQLite나 목(mock)으로 재현할 수 없으므로, 통합 테스트는 실제 PostgreSQL에서 실행해야 한다.
- 동시성 테스트는 여러 연결이 각자 커밋하는 상황을 만들어야 한다.
- 스키마의 기준은 Alembic 마이그레이션이다. 테스트가 마이그레이션 자체도 검증해야 한다.
- 개발용 DB의 데이터가 테스트 때문에 지워지면 안 된다.

## 결정

### DB 분리

- 개발과 같은 PostgreSQL 서버(로컬은 compose `db`, CI는 같은 버전의 서비스 컨테이너)에 별도 데이터베이스 `registry_test`를 쓴다. 접속 정보는 `TEST_DATABASE_URL` 환경 변수로 바꿀 수 있다.
- 실수로 개발 DB를 지우지 않도록, DB 이름이 `_test`로 끝나지 않으면 테스트 세션을 시작하지 않는다.

### 로컬 실행 절차

- 기본 `compose.yaml`은 DB 포트를 호스트에 노출하지 않는다. DB는 compose 내부 네트워크에서 API와 migrate만 접근한다.
- 호스트에서 `pytest`를 실행할 때만 DB 포트를 노출하는 `compose.dev.yaml`을 겹쳐 쓴다. 노출 포트는 `DB_PORT`(기본 5432)로 바꿀 수 있다.

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d db
uv run pytest
```

- 파일 이름을 `compose.override.yaml`로 하지 않는다. 이 이름은 `docker compose up` 시 자동으로 병합되어, 기본 실행에서도 DB 포트가 노출되기 때문이다.
- `TEST_DATABASE_URL` 기본값은 `localhost:${DB_PORT}`의 `registry_test`를 가리킨다.

### 스키마 준비

- 테스트 세션이 시작될 때 `registry_test`가 없으면 만들고, `alembic upgrade head`로 스키마를 만든다.
- `Base.metadata.create_all()`은 쓰지 않는다. 테스트가 운영과 같은 마이그레이션 경로를 거치게 하기 위해서다.

### 테스트 간 격리

- 각 테스트가 끝나면 `alembic_version`을 제외한 모든 테이블을 `TRUNCATE ... RESTART IDENTITY CASCADE`로 비운다.
- 테스트 중에는 실제로 커밋한다. service 계층의 트랜잭션 코드(커밋, 롤백, 행 잠금)가 운영과 똑같이 실행된다.

### 마이그레이션 검증

- `upgrade head → downgrade base → upgrade head`가 성공하는지 확인한다.
- ORM 엔티티와 마이그레이션 결과 스키마가 어긋나지 않는지 Alembic `compare_metadata`로 확인한다. autogenerate 결과가 비어 있어야 한다.

### 테스트 구분

- `tests/unit`: DB 없이 실행하는 순수 로직 테스트(복원 기한 계산, 커서 인코딩 등)
- `tests/integration`: API와 실제 PostgreSQL을 함께 쓰는 테스트

## 검토한 대안

### 테스트마다 트랜잭션 롤백 (외부 트랜잭션 + SAVEPOINT)

- 장점: 테이블을 비우지 않아 빠르다.
- 단점: 테스트 코드의 커밋이 실제로 커밋되지 않으므로, 다른 연결에서 데이터를 볼 수 없다. 동시성 테스트가 불가능하고, 트랜잭션 경계 검증이 운영과 달라진다.

### SQLite 인메모리

- 장점: 외부 의존성이 없고 가장 빠르다.
- 단점: 행 잠금, JSONB, `timestamptz`가 없거나 다르게 동작한다. 통과해도 운영 동작을 보장하지 못한다.

### Testcontainers (테스트 세션마다 PostgreSQL 컨테이너 생성)

- 장점: 테스트마다 깨끗한 DB를 쓰고, 사전 준비 없이 `pytest`만으로 실행된다.
- 단점: 테스트 실행 환경에 Docker 소켓 접근이 필요하고, 세션마다 컨테이너를 띄우는 시간이 든다. 의존성이 하나 늘어난다. compose `db`와 CI 서비스 컨테이너로 같은 목적을 이룰 수 있으므로 채택하지 않는다.

### 테스트마다 스키마 삭제 후 재생성

- 장점: 완전히 깨끗한 상태에서 시작한다.
- 단점: 테스트마다 마이그레이션 전체를 실행해야 해서 느리다.

## 결과

- 통합 테스트가 운영과 같은 DB 엔진, 같은 마이그레이션, 같은 트랜잭션 동작 위에서 실행된다.
- 개발 데이터와 테스트 데이터가 섞이지 않는다.
- 트레이드오프
  - 로컬에서는 테스트 전에 `compose.dev.yaml`을 겹쳐 DB를 띄워야 한다. README와 CLAUDE.md에 명시한다.
  - TRUNCATE는 테스트마다 수 밀리초가 든다. 테이블이 적은 지금은 문제가 없고, 테스트가 많아지면 동시성 테스트만 TRUNCATE를 쓰고 나머지는 롤백 방식으로 나누는 혼합 전략을 검토한다.
  - 테스트들이 같은 DB를 공유하므로 `pytest-xdist` 병렬 실행은 그대로 쓸 수 없다. 필요해지면 워커별 DB(`registry_test_gw0` 등)로 확장한다.
