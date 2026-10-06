# AutoML 플랫폼 ERD

AutoML 플랫폼 전체 데이터 모델. 이 저장소는 이 중 `users`, `models`, `model_versions`를 구현한다. 스키마의 기준(source of truth)은 Alembic 마이그레이션이며, 이 문서는 설계 의도를 설명한다.

## 설계 원칙
- 파일(데이터 원본, 모델 아티팩트)은 오브젝트 스토리지에 저장하고 DB에는 메타데이터와 URI만 저장
- 데이터와 모델은 "이름표(datasets/models) + 불변 버전(*_versions)" 구조. 수정/추가 시 덮어쓰지 않고 새 버전 생성 → 재현성 확보
- 모델 삭제는 소프트 삭제(`deleted_at`) + 복원 기한(`purge_at`)
- 모델별로 다른 입출력은 `model_versions.input_schema / output_schema` (JSONB, JSON Schema 형식)으로 관리
- 학습 상태는 `training_jobs.status` + 이력 테이블 `training_job_events`로 추적, 장시간 학습 대비 체크포인트/재시도 컬럼 보유
- 추론 요청/응답 로그는 양이 많아 RDB에 두지 않고 데이터 레이크(Parquet)로 적재 → 서비스 DB와 분석 부하 분리

## 테이블 요약
| 영역 | 테이블 | 역할 |
|---|---|---|
| 사용자 | users | 고객/내부 사용자, role로 구분 |
| 데이터 | datasets | 데이터셋 이름표, 소프트 삭제 |
| 데이터 | dataset_versions | 업로드 파일 1건, 원본/Parquet URI, 크기, 체크섬, 업로드 상태 |
| 데이터 | dataset_columns | 버전별 컬럼명, 타입 |
| 모델 | models | 모델 이름표, 소프트 삭제 + 복원 기한 |
| 모델 | model_versions | 학습 결과물 v1, v2…, 부모 버전, 입출력 스키마, 아티팩트 URI |
| 모델 | model_version_datasets | 버전 ↔ 데이터 버전 N:M (학습 데이터 계보) |
| 모델 | model_metrics | 성능 지표 (행 단위, 버전 간 비교 용이) |
| 학습 | training_jobs | 학습 작업 (버전과 1:1), 상태, GPU, K8s Job, 체크포인트, 재시도 |
| 학습 | training_job_events | 학습 상태 변경 이력 |
| 서빙 | deployments | 모델별 엔드포인트, active_version_id 교체로 배포/롤백 |

## 다이어그램

```mermaid
erDiagram
    users ||--o{ datasets : owns
    users ||--o{ models : owns
    users ||--o{ training_jobs : requests
    datasets ||--|{ dataset_versions : has
    dataset_versions ||--|{ dataset_columns : has
    models ||--o{ model_versions : has
    model_versions ||--o{ model_versions : "derived_from"
    model_versions ||--|{ model_version_datasets : "trained_on"
    dataset_versions ||--o{ model_version_datasets : "used_in"
    model_versions ||--|| training_jobs : "produced_by"
    training_jobs ||--o{ training_job_events : logs
    model_versions ||--o{ model_metrics : has
    models ||--o| deployments : "served_by"
    model_versions ||--o{ deployments : "active_in"

    users {
        uuid id PK
        string email UK
        string name
        string role "CUSTOMER|ML_ENGINEER|ADMIN"
        timestamptz created_at
    }
    datasets {
        uuid id PK
        uuid owner_id FK
        string name
        text description
        timestamptz created_at
        timestamptz updated_at
        timestamptz deleted_at
    }
    dataset_versions {
        uuid id PK
        uuid dataset_id FK
        int version_no "UK(dataset_id, version_no)"
        uuid base_version_id FK "nullable"
        string source_type "UPLOAD|APPEND|EDIT"
        string file_format "CSV|XLSX"
        string original_uri
        string parquet_uri
        bigint size_bytes
        string checksum_sha256
        bigint row_count
        string status "UPLOADING|VALIDATING|READY|FAILED"
        timestamptz created_at
    }
    dataset_columns {
        uuid id PK
        uuid dataset_version_id FK
        int ordinal
        string name "UK(dataset_version_id, name)"
        string dtype
        boolean nullable
    }
    models {
        uuid id PK
        uuid owner_id FK
        string name "partial UK(owner_id, name) where deleted_at is null"
        string task_type "CLASSIFICATION|REGRESSION"
        text description
        timestamptz created_at
        timestamptz updated_at
        timestamptz deleted_at
        timestamptz purge_at
    }
    model_versions {
        uuid id PK
        uuid model_id FK
        int version_no "UK(model_id, version_no)"
        uuid parent_version_id FK "nullable"
        string status "PENDING|TRAINING|READY|FAILED|ARCHIVED"
        string artifact_uri
        string framework
        jsonb input_schema
        jsonb output_schema
        uuid created_by FK
        timestamptz created_at
    }
    model_version_datasets {
        uuid model_version_id PK, FK
        uuid dataset_version_id PK, FK
        string split_role "TRAIN|VALID|TEST"
    }
    training_jobs {
        uuid id PK
        uuid model_version_id FK, UK
        uuid requested_by FK
        string status "QUEUED|PROVISIONING|RUNNING|SUCCEEDED|FAILED|CANCELLED"
        string target_column
        jsonb feature_columns
        jsonb hyperparameters
        string gpu_type
        int gpu_count
        string k8s_job_name
        int attempt
        int max_attempts
        string checkpoint_uri
        string error_code
        text error_message
        timestamptz queued_at
        timestamptz started_at
        timestamptz finished_at
    }
    training_job_events {
        bigint id PK
        uuid training_job_id FK
        string from_status
        string to_status
        text message
        timestamptz created_at
    }
    model_metrics {
        bigint id PK
        uuid model_version_id FK
        string split "VALID|TEST"
        string metric_name "UK(version, split, name)"
        double metric_value
    }
    deployments {
        uuid id PK
        uuid model_id FK, UK
        uuid active_version_id FK
        string endpoint_path
        string status "ACTIVE|INACTIVE"
        int replicas
        timestamptz updated_at
    }
```
