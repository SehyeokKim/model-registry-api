# 0005. 복원 기한이 지난 모델 영구 삭제

- 상태: 채택됨
- 날짜: 2026-10-06

## 배경

- 모델 삭제는 소프트 삭제다. 삭제 시 `deleted_at`과 `purge_at`(`deleted_at + MODEL_RESTORE_WINDOW_DAYS`, 기본 30일)을 기록하고, `purge_at` 전까지만 복원할 수 있다.
- 복원 기한이 지난 모델을 계속 보관할지 정해야 한다. 판단 근거는 중요한 순서대로 다음과 같다.
  1. **고객 데이터 파기 의무**: 고객은 제조, 에너지, 물류 분야 기업이다([0002](0002-sync-sqlalchemy.md)의 규모 가정). 기업 고객과의 계약에는 보통 "고객이 삭제를 요청하거나 계약이 끝나면 데이터를 파기한다"는 조항이 들어간다. 고객 데이터로 학습한 모델 아티팩트에는 공정 조건, 설비 운영 패턴 같은 영업 비밀이 담긴다. 고객이 삭제했고 복원 기한도 지난 데이터를 계속 보관하면 이 의무를 지킬 수 없다.
  2. **개인정보 처리 규정**: 학습 데이터나 모델 이름, 설명에 개인정보가 섞일 수 있다. 한국 개인정보 보호법은 보유 목적이 끝난 개인정보를 지체 없이 파기하도록 하고, 일본 개인정보보호법(APPI) 등 다른 국가의 법도 필요 없어진 개인정보를 계속 보관하지 않는 것을 원칙으로 한다.
  3. **오브젝트 스토리지 비용**: 실제 비용이 큰 것은 DB 행이 아니라 오브젝트 스토리지에 남는 모델 아티팩트와 체크포인트다. 버전마다 수백 MB–수 GB가 쌓인다.
  4. **DB 용량**: 고객사 수십–수백 곳 규모에서는 삭제된 모델 행이 쌓여도 DB 용량과 조회 성능에 주는 영향은 작다. 활성 모델 이름 중복 방지 인덱스가 `WHERE deleted_at IS NULL` 부분 인덱스라 활성 모델 조회에는 거의 영향이 없다. 따라서 DB 용량은 영구 삭제의 주된 이유가 아니다.

## 결정

### 영구 삭제 커맨드

- `scripts/purge_expired_models.py`가 `purge_at <= now()`인 모델을 영구 삭제한다. API 서버와 같은 이미지에서 `python -m scripts.purge_expired_models`로 실행한다.
- 배치 단위로 삭제하고, 배치마다 별도 트랜잭션으로 커밋한다. 삭제할 행이 없을 때까지 반복한다.

```sql
DELETE FROM models
WHERE id IN (
    SELECT id FROM models
    WHERE deleted_at IS NOT NULL AND purge_at <= now()
    ORDER BY purge_at
    LIMIT :batch_size
    FOR UPDATE SKIP LOCKED
)
RETURNING id, organization_id;
```

- 배치 크기는 `PURGE_BATCH_SIZE`(기본 500)로 조정한다. 트랜잭션을 짧게 끊어 잠금 시간과 WAL 급증을 줄인다.
- 대상 조회는 `ix_models_purge_at` 부분 인덱스(`WHERE deleted_at IS NOT NULL`)를 쓴다.
- `model_versions`는 FK `ON DELETE CASCADE`로 함께 삭제된다.
- `--dry-run` 옵션은 삭제 없이 대상 수만 출력한다.
- 삭제한 모델 ID, 소유 조직 ID, 건수를 로그로 남긴다. 감사 기록이자 고객사별 파기 증빙, 아티팩트 정리의 입력이 된다.

### 복원과의 경합

- 복원은 모델 행을 `FOR NO KEY UPDATE`로 잠그고 `purge_at`을 확인한다([0003](0003-version-number-concurrency.md)).
- 영구 삭제는 `FOR UPDATE SKIP LOCKED`로 대상을 고른다. 복원 트랜잭션이 잠근 행은 이번 배치에서 건너뛰고 다음 실행에서 처리한다.
- 어느 쪽이 먼저 잠금을 잡든 결과가 일관된다.
  - 복원이 먼저: 기한이 지났으므로 410을 반환하고, 영구 삭제는 다음 실행에서 처리한다.
  - 영구 삭제가 먼저: 복원은 잠금을 기다린 뒤 행이 없으므로 404를 반환한다.
- `SKIP LOCKED` 덕분에 영구 삭제 작업이 여러 개 겹쳐 실행되어도 같은 행을 두고 대기하지 않는다.

### 응답 규칙

| 상황 | 복원 요청 응답 |
|---|---|
| 기한 전 | 200 (복원) |
| 기한이 지났지만 아직 영구 삭제 전 | 410 `MODEL_PURGED` |
| 영구 삭제 후 | 404 `MODEL_NOT_FOUND` |

삭제된 모델은 기한과 관계없이 조회와 수정에서 404다.

### 실행 환경

- 운영: Kubernetes CronJob으로 매일 저부하 시간에 실행한다. `concurrencyPolicy: Forbid`로 중복 실행을 막는다.
- 아티팩트 삭제: 오브젝트 스토리지 정리는 이벤트 기반으로 처리하며, 설계 문서에서 상세화한다. 아티팩트 경로가 UUID 기반(`models/{model_id}/...`, [0003](0003-version-number-concurrency.md))이므로, DB 행을 지운 뒤에도 모델 ID만으로 정리 대상을 찾을 수 있다.
- 로컬: docker compose에는 스케줄러가 없으므로 수동으로 실행한다. 방법은 README에 쓴다.

```bash
docker compose exec api python -m scripts.purge_expired_models --dry-run
docker compose exec api python -m scripts.purge_expired_models
```

## 검토한 대안

### 기한이 지나도 보관 (소프트 삭제 상태 유지)

- 장점: 구현할 것이 없고, 실수로 지운 모델도 운영자가 되살릴 수 있다.
- 단점: 기한이 지난 고객 데이터를 보관하게 되어 파기 의무를 지킬 수 없고, 아티팩트 저장 비용이 계속 늘어난다.

### 기한이 지난 행을 아카이브 테이블로 이동

- 장점: 운영 테이블은 작게 유지된다.
- 단점: 고객 데이터 파기 의무와 개인정보 보관 문제는 그대로다.

### PostgreSQL 확장 `pg_cron`으로 DB 안에서 실행

- 장점: 별도 스케줄러가 필요 없다.
- 단점: 스케줄과 로직이 DB에 묶여 코드 리뷰, 테스트, 배포 경로에서 벗어난다. 관리형 DB에서는 확장 지원 여부도 확인해야 한다. 로그와 실패 알림을 다른 워크로드와 같은 방식으로 다룰 수 없다.

### 삭제 시점 기준 파티셔닝 후 파티션 단위 삭제

- 장점: 대량 삭제가 `DROP PARTITION` 한 번으로 끝난다.
- 단점: 행이 삭제 시점에 다른 파티션으로 옮겨져야 하고, partial unique index 같은 제약 설계가 복잡해진다. 현재 규모에는 과하다.

## 결과

- 기한이 지난 모델은 DB에서 사라지고, 용량은 활성 모델과 복원 기간 중인 모델 수에 비례한다.
- 트레이드오프
  - 영구 삭제는 되돌릴 수 없다. 기한 전 복원 기능과 `--dry-run`, 삭제 로그로 실수를 줄인다.
  - CronJob 실행 주기만큼 기한이 지난 모델이 남아 있을 수 있다. 이 기간에는 410으로 응답한다.
  - 앞으로 `models`나 `model_versions`를 참조하는 테이블(`training_jobs`, `deployments` 등)을 추가할 때는 `ON DELETE` 동작이나 사전 정리 순서를 함께 정해야 한다. 그렇지 않으면 영구 삭제가 FK 위반으로 실패한다.
