# Kubernetes 참조 매니페스트

운영 환경(Kubernetes)에서 이 서비스를 어떻게 실행하는지 보여주는 참조 매니페스트입니다.
로컬 실행은 저장소 루트의 `docker compose up -d`를 씁니다. 클러스터 배포 파이프라인(CD)은 이 저장소의 범위 밖입니다.

| 파일 | 리소스 | 근거 |
|---|---|---|
| `configmap.yaml` | 비밀이 아닌 설정 (`.env.example`과 같은 변수) | |
| `migrate-job.yaml` | 배포 전 마이그레이션 Job (Argo CD `PreSync` hook) | ADR 0001 |
| `deployment.yaml` | API Deployment (Pod당 워커 1개, liveness, readiness, startup probe) | ADR 0002 |
| `service.yaml` | 클러스터 내부 Service (ClusterIP) | |
| `hpa.yaml` | HPA 2–8개 (DB 연결 예산으로 상한 결정) | ADR 0002 |
| `purge-cronjob.yaml` | 복원 기한이 지난 모델 영구 삭제, 매일 03:00 KST, `concurrencyPolicy: Forbid` | ADR 0005 |

## 검증 범위

- CI의 Lint job이 [kubeconform](https://github.com/yannh/kubeconform)으로 Kubernetes 1.30 스키마를 검증합니다. **문법과 필드 검증만 하며 클러스터에 배포하지는 않습니다.**
- 매니페스트와 앱이 어긋나지 않는지는 `tests/unit/test_k8s_manifests.py`가 확인합니다.
  - 환경 변수 이름이 앱 설정에 있는지
  - probe 경로가 실제 엔드포인트인지
  - 실행 사용자가 이미지의 사용자와 같은지
  - Job 명령이 실제로 있는지
  - HPA 상한이 DB 연결 예산 공식을 지키는지
- 읽기 전용 루트 파일 시스템, non-root, 권한 제거 설정은 같은 조건의 `docker run`으로 마이그레이션, 영구 삭제 커맨드, API 기동을 확인했습니다.

## 배포 전에 준비할 것

Secret은 저장소에 커밋하지 않습니다. 운영에서는 External Secrets Operator 같은 비밀 관리 도구로 주입하고, 직접 만든다면 다음과 같습니다.

```bash
# 마이그레이션 계정: DDL 권한 (ADR 0001)
kubectl create secret generic model-registry-db-migrator \
  --from-literal=database-url='postgresql+psycopg://migrator:<password>@<db-host>:5432/registry'

# API, 영구 삭제 커맨드 계정: DML 권한만
kubectl create secret generic model-registry-db-app \
  --from-literal=database-url='postgresql+psycopg://app:<password>@<db-host>:5432/registry'

# GHCR 이미지 pull (패키지가 private)
kubectl create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io --docker-username=<user> --docker-password=<read:packages 토큰>
```

## 이미지 태그

매니페스트의 이미지는 `ghcr.io/sehyeokkim/model-registry-api:COMMIT_SHA` 자리표시자입니다. main에서 CI가 통과한 커밋은 Publish image 워크플로가 커밋 SHA 태그로 게시하고, 배포 파이프라인이 그 SHA로 바꿔 넣습니다.
`latest`처럼 덮어쓰이는 태그를 쓰지 않아, 클러스터에 어떤 커밋이 떠 있는지 항상 정확히 알 수 있습니다.

## 적용 순서

Argo CD로 동기화하면 `PreSync` hook인 마이그레이션 Job이 먼저 실행되고, Job이 성공해야 나머지 리소스가 적용됩니다.
Argo CD 없이 직접 적용할 때는 같은 순서를 지킵니다.

```bash
kubectl apply -f deploy/k8s/migrate-job.yaml
kubectl wait --for=condition=complete job/model-registry-migrate --timeout=600s
kubectl apply -f deploy/k8s/configmap.yaml -f deploy/k8s/deployment.yaml \
  -f deploy/k8s/service.yaml -f deploy/k8s/hpa.yaml -f deploy/k8s/purge-cronjob.yaml
```

## 포함하지 않은 것

다음은 클러스터 구성과 함께 정할 사항이라 설계 문서에서 다룹니다.

- 외부 노출: Ingress 또는 API Gateway 라우팅. 내부 API(`/internal/v1`)는 외부에 노출하지 않습니다.
- PodDisruptionBudget, NetworkPolicy, 네임스페이스와 RBAC
- 환경별 설정 분리(kustomize overlay, Helm values)
