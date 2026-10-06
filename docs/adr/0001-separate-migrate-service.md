# 0001. DB 마이그레이션을 일회성 migrate 서비스로 분리

- 상태: 채택됨
- 날짜: 2026-10-06

## 배경

- 저장소 루트에서 `docker compose up -d` 한 번으로 DB와 API 서버가 기동되어야 하고, 기동 시 DB 마이그레이션이 자동으로 실행되어야 한다.
- 운영 환경은 Kubernetes이며 API는 여러 레플리카로 실행된다. 배포 중에는 이전 버전 Pod와 새 버전 Pod가 함께 떠 있다.
- API 프로세스가 기동할 때 각자 마이그레이션을 실행하면 다음 문제가 생긴다.
  - 레플리카 여러 개가 같은 DDL을 동시에 실행하는 경쟁 상태가 생긴다. Alembic은 기본적으로 동시 실행을 막는 잠금을 걸지 않는다.
  - 마이그레이션이 실패하면 모든 Pod가 기동에 실패하고 재시작을 반복한다. 원인이 API 로그 사이에 섞여 찾기 어렵다.
  - API의 DB 계정에 DDL 권한이 필요해진다.
- 데모 데이터(조직과 사용자)도 기동 시 들어가야 하지만, 운영 DB에는 들어가면 안 된다.

## 결정

- 마이그레이션은 API 컨테이너와 분리된 일회성 `migrate` 서비스가 수행한다. API와 같은 이미지를 쓰고 실행 명령만 다르다.
  - 실행 순서: `alembic upgrade head` → 데모 데이터 시드(`python -m scripts.seed`)
  - `restart: "no"`로 한 번만 실행하고 종료한다.
- compose 의존 관계는 다음과 같다.
  - `migrate`는 `db`가 `service_healthy`가 된 뒤 시작한다.
  - `api`는 `migrate`가 `service_completed_successfully`로 끝난 뒤 시작한다.
- 데모 데이터는 스키마 마이그레이션에 넣지 않고 별도 시드 스크립트로 넣는다.
  - 시드는 `INSERT ... ON CONFLICT DO NOTHING`으로 멱등하게 동작하므로 여러 번 실행해도 결과가 같다.
  - `SEED_DEMO_DATA=false`로 끌 수 있다.
- Kubernetes에서는 같은 구조를 배포 전 실행되는 마이그레이션 Job(Helm `pre-upgrade` hook 또는 Argo CD `PreSync` hook)으로 옮긴다. Job이 성공해야 Deployment 롤아웃이 진행된다.

## 검토한 대안

### API 기동 시 마이그레이션 실행 (FastAPI lifespan 또는 컨테이너 entrypoint 스크립트)

- 장점: 서비스가 하나 줄고 구성이 가장 단순하다.
- 단점: 레플리카 수만큼 마이그레이션이 동시에 실행된다. 실패하면 API가 아예 뜨지 않고, 기동 시간도 마이그레이션 시간만큼 늘어난다.

### API 기동 시 실행하되 PostgreSQL advisory lock으로 직렬화

- 장점: 경쟁 상태는 해결된다.
- 단점: API와 스키마 변경이 계속 묶여 있다. API 계정에 DDL 권한이 필요하고, 잠금을 기다리는 Pod들의 기동이 늦어진다.

### Kubernetes initContainer

- 장점: Pod 정의 안에서 끝나므로 별도 Job 관리가 필요 없다.
- 단점: Pod마다 실행되므로 결국 advisory lock 같은 직렬화 장치가 필요하다. 스케일 아웃할 때마다 불필요하게 다시 실행된다.

## 결과

- 마이그레이션은 배포당 한 번만, 한 곳에서 실행된다. 레플리카 수와 관계없이 경쟁 상태가 없다.
- 마이그레이션이 실패하면 API가 시작되지 않는다. `docker compose up -d`가 오류를 보고하고, 원인은 `docker compose logs migrate`에서 바로 확인할 수 있다.
- 마이그레이션 계정(DDL)과 API 계정(DML)을 나눌 수 있는 구조가 된다. 지금은 같은 계정을 쓰고, 운영 환경에서 분리한다.
- 트레이드오프
  - compose 서비스가 하나 늘고, `docker compose ps`에서 `migrate`가 `exited (0)`으로 표시된다. 정상 동작이며 README에 설명한다.
  - 롤링 배포 중에는 이전 버전 API가 새 스키마 위에서 동작한다. 따라서 마이그레이션은 하위 호환을 유지해야 한다. 컬럼 삭제나 이름 변경은 확장(expand) 배포와 축소(contract) 배포로 나눈다.
