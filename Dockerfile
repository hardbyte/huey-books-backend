################################################################################
# Builder: resolve dependencies into a venv using uv. uv lives only here.
################################################################################
FROM python:3.11-slim AS builder

# uv binary from the official (pinned) image — not shipped to the runtime stage.
COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
  UV_LINK_MODE=copy \
  UV_PYTHON_DOWNLOADS=0 \
  UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Allow installing dev dependencies to run tests.
ARG INSTALL_DEV=false

# Install dependencies from the lockfile only (no app code) so this layer is
# cached unless pyproject.toml / uv.lock change. The project itself is not a
# package (tool.uv package = false); it runs from source on PYTHONPATH.
COPY pyproject.toml uv.lock ./
# No BuildKit cache mount: the prod Cloud Build / local harness use the classic
# docker builder. Docker layer caching still skips this when the lock is
# unchanged, and uv installs fast regardless.
RUN if [ "$INSTALL_DEV" = "true" ]; then \
  uv sync --frozen --no-install-project; \
  else \
  uv sync --frozen --no-install-project --no-dev; \
  fi

################################################################################
# Runtime: slim image carrying only the venv + app. No uv, no poetry, no caches.
################################################################################
FROM python:3.11-slim AS runtime

ENV USERNAME=wriveted \
  USER_UID=1000 \
  USER_GID=1000 \
  PYTHONPATH=/app \
  PYTHONUNBUFFERED=1 \
  PORT=8000 \
  VIRTUAL_ENV=/opt/venv \
  PATH="/opt/venv/bin:$PATH"

LABEL org.opencontainers.image.source=https://github.com/hardbyte/huey-books-backend

# hadolint ignore=DL3008
RUN apt-get update \
  && apt-get install --no-install-recommends -y curl \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd --gid ${USER_GID} ${USERNAME} \
  && useradd --uid ${USER_UID} --gid ${USER_GID} -m ${USERNAME}

# The resolved virtual environment (deps + precompiled bytecode).
COPY --from=builder --chown=${USER_UID}:${USER_GID} /opt/venv /opt/venv

WORKDIR /app
# pyproject.toml carries pytest config ([tool.pytest.ini_options], incl.
# asyncio_mode=auto) and ruff config used by the test image — keep it at /app.
COPY --chown=${USER_UID}:${USER_GID} pyproject.toml alembic.ini ./
COPY --chown=${USER_UID}:${USER_GID} scripts/ /app/scripts
COPY --chown=${USER_UID}:${USER_GID} alembic/ /app/alembic
COPY --chown=${USER_UID}:${USER_GID} app/ /app/app

USER ${USERNAME}

# Run the ASGI app as a single uvicorn process; Cloud Run scales by instance,
# so a process supervisor (gunicorn) adds no value here. sh -c expands $PORT
# (Cloud Run sets it) and forwards the app path from CMD, or from a Cloud Run
# --args override (e.g. the internal service uses app.internal_api:internal_app).
# exec makes uvicorn PID 1 for signal handling; an import/boot failure exits
# non-zero so the revision never goes healthy instead of serving a broken app.
ENTRYPOINT ["sh", "-c", "exec uvicorn \"$@\" --host 0.0.0.0 --port \"${PORT:-8080}\"", "sh"]
CMD ["app.main:app"]
