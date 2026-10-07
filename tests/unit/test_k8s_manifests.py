"""Kubernetes 매니페스트가 앱, 이미지, ADR과 어긋나지 않는지 확인한다.

kubeconform(CI)은 스키마 문법만 검사한다. 환경 변수 이름, probe 경로, 실행 사용자처럼
앱 코드와 맞아야 하는 값은 여기서 확인한다.
"""

import importlib.util
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = ROOT / "deploy" / "k8s"
IMAGE = "ghcr.io/sehyeokkim/model-registry-api:COMMIT_SHA"
# ADR 0002: 마이그레이션, 영구 삭제, 운영 접속에 남겨 두는 연결 수와 PostgreSQL 기본 max_connections
RESERVED_CONNECTIONS = 15
MAX_CONNECTIONS = 100


def _resources() -> dict[str, dict[str, Any]]:
    docs = (yaml.safe_load(path.read_text(encoding="utf-8")) for path in MANIFESTS.glob("*.yaml"))
    return {doc["kind"]: doc for doc in docs}


def _pod_spec(resource: dict[str, Any]) -> dict[str, Any]:
    spec = resource["spec"]
    if resource["kind"] == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    return spec["template"]["spec"]


def _containers() -> Iterator[tuple[str, dict[str, Any]]]:
    for kind, resource in _resources().items():
        if kind in {"Deployment", "Job", "CronJob"}:
            for container in _pod_spec(resource)["containers"]:
                yield kind, container


def test_every_manifest_is_known() -> None:
    assert set(_resources()) == {
        "ConfigMap",
        "Deployment",
        "Service",
        "HorizontalPodAutoscaler",
        "Job",
        "CronJob",
    }


def test_environment_variables_are_app_settings() -> None:
    settings = {name.upper() for name in Settings.model_fields}
    names = set(_resources()["ConfigMap"]["data"])
    for _, container in _containers():
        names |= {env["name"] for env in container.get("env", [])}

    assert names - settings == set()


def test_all_workloads_use_the_published_image() -> None:
    assert {container["image"] for _, container in _containers()} == {IMAGE}


def test_pods_run_as_image_user() -> None:
    uid = int(re.search(r"--uid (\d+)", (ROOT / "Dockerfile").read_text(encoding="utf-8")).group(1))  # type: ignore[union-attr]

    for kind in ("Deployment", "Job", "CronJob"):
        context = _pod_spec(_resources()[kind])["securityContext"]
        assert context["runAsNonRoot"] is True, kind
        assert context["runAsUser"] == uid, kind


def test_probes_point_to_health_endpoints(app: FastAPI) -> None:
    paths = set(app.openapi()["paths"])
    container = _pod_spec(_resources()["Deployment"])["containers"][0]

    assert container["livenessProbe"]["httpGet"]["path"] == "/health/live"
    assert container["readinessProbe"]["httpGet"]["path"] == "/health/ready"
    assert {"/health/live", "/health/ready"} <= paths


@pytest.mark.parametrize("kind", ["Job", "CronJob"])
def test_job_commands_exist(kind: str) -> None:
    command = _pod_spec(_resources()[kind])["containers"][0]["command"]

    if command[:2] == ["python", "-m"]:
        assert importlib.util.find_spec(command[2]) is not None
    else:
        assert command == ["alembic", "upgrade", "head"]


def test_migrate_job_runs_before_rollout_without_config_map() -> None:
    """ADR 0001: Job이 성공해야 롤아웃이 진행된다. PreSync hook은 ConfigMap보다 먼저 실행된다."""
    job = _resources()["Job"]
    container = _pod_spec(job)["containers"][0]

    assert job["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
    assert "envFrom" not in container


def test_migration_and_api_use_separate_db_accounts() -> None:
    """ADR 0001: 마이그레이션 계정(DDL)과 API 계정(DML)을 나눈다."""

    def secret(kind: str) -> str:
        env = _pod_spec(_resources()[kind])["containers"][0]["env"]
        database_url = next(e for e in env if e["name"] == "DATABASE_URL")
        return database_url["valueFrom"]["secretKeyRef"]["name"]

    assert secret("Job") != secret("Deployment")
    assert secret("CronJob") == secret("Deployment")


def test_hpa_stays_within_db_connection_budget() -> None:
    """ADR 0002: HPA 최대 Pod 수 * Pod당 최대 연결 수 + 여유 <= max_connections"""
    config = _resources()["ConfigMap"]["data"]
    per_pod = int(config["DB_POOL_SIZE"]) + int(config["DB_MAX_OVERFLOW"])
    max_pods = _resources()["HorizontalPodAutoscaler"]["spec"]["maxReplicas"]

    assert max_pods * per_pod + RESERVED_CONNECTIONS <= MAX_CONNECTIONS


def test_purge_cronjob_does_not_overlap() -> None:
    """ADR 0005: 이전 실행이 끝나지 않았으면 새 실행을 건너뛴다."""
    assert _resources()["CronJob"]["spec"]["concurrencyPolicy"] == "Forbid"
