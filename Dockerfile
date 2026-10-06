# syntax=docker/dockerfile:1

# API 서버와 migrate 서비스가 같은 이미지를 쓴다 (실행 명령만 다름, ADR 0001).
ARG PYTHON_IMAGE=python:3.12-slim

FROM ghcr.io/astral-sh/uv:0.12.2 AS uv

# ---------- builder: 런타임 의존성만 담은 가상환경을 만든다 ----------
FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# 의존성 파일만으로 설치해, 소스가 바뀌어도 이 레이어는 캐시를 재사용한다.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=.python-version,target=.python-version \
    uv sync --locked --no-dev --no-install-project

# ---------- runtime: 가상환경과 실행에 필요한 파일만 담는다 ----------
FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="model-registry-api" \
      org.opencontainers.image.description="Model management API for an AutoML platform" \
      org.opencontainers.image.source="https://github.com/SehyeokKim/model-registry-api"

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# 파일 소유자는 root로 두어 실행 사용자(app)가 코드를 바꿀 수 없게 한다.
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY app ./app

USER app

EXPOSE 8000

# slim 이미지에는 curl이 없으므로 Python으로 확인한다. DB 확인은 compose/K8s readiness가 맡는다.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]

# 워커는 1개로 두고 확장은 컨테이너(Pod) 수로 한다 (ADR 0002).
# 요청 로그는 앱의 JSON 접근 로그가 남기므로 uvicorn 접근 로그는 끈다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
