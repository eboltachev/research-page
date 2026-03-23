FROM python:3.12-slim

ARG APPLICATION_WORK_DIR=/app
ARG API_HOST=0.0.0.0
ARG API_PORT=8000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

RUN set -eux; \
    groupadd -g 1000 cx; \
    useradd -m -u 1000 -g 1000 -s /bin/bash cx; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    pip install --no-cache-dir uv; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR ${APPLICATION_WORK_DIR}

COPY --chown=cx:cx pyproject.toml uv.lock .python-version ./

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip sync uv.lock

COPY --chown=cx:cx app ./app
COPY --chown=cx:cx configs ./configs
COPY --chown=cx:cx README.md ./

USER cx

EXPOSE 8000

ENTRYPOINT ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
