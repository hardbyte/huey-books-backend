#!/usr/bin/env bash

# Set bash to exit immediately on any command failure
set -e

POOL_SIZE=2
MAX_OVERFLOW=0

for arg in "$@"; do
  if [[ "${arg}" == "--run-isolated-tests" ]]; then
    POOL_SIZE=10
    MAX_OVERFLOW=0
    break
  fi
done

export DATABASE_POOL_SIZE="${POOL_SIZE}"
export DATABASE_MAX_OVERFLOW="${MAX_OVERFLOW}"

# Allow local-only builds without remote cache pulls (avoids gcloud auth).
# Set LOCAL_BUILD_ONLY=1 to skip cache_from usage.
COMPOSE_FILES=(-f docker-compose.yml)
BUILD_ARGS=(--build-arg INSTALL_DEV=true)
NO_CACHE_FILE=""
LOCAL_DOCKER_CONFIG=""

if [[ "${LOCAL_BUILD_ONLY:-}" == "1" ]]; then
  export DOCKER_BUILDKIT=0
  export COMPOSE_DOCKER_CLI_BUILD=0
  LOCAL_DOCKER_CONFIG="$(mktemp -d)"
  export DOCKER_CONFIG="${LOCAL_DOCKER_CONFIG}"
  NO_CACHE_FILE="$(mktemp)"
  cat > "${NO_CACHE_FILE}" <<'YAML'
services:
  migration:
    build:
      cache_from: []
  api:
    build:
      cache_from: []
  internal:
    build:
      cache_from: []
YAML
  COMPOSE_FILES+=(-f "${NO_CACHE_FILE}")
  if [[ "${NO_DOCKER_CACHE:-}" == "1" ]]; then
    BUILD_ARGS+=(--no-cache)
  fi
else
  # Env vars to enable buildkit for docker-compose
  export DOCKER_BUILDKIT=1
  export COMPOSE_DOCKER_CLI_BUILD=1
  BUILD_ARGS+=(--build-arg BUILDKIT_INLINE_CACHE=1)
fi

cleanup() {
  if [[ -n "${NO_CACHE_FILE}" && -f "${NO_CACHE_FILE}" ]]; then
    rm -f "${NO_CACHE_FILE}"
  fi
  if [[ -n "${LOCAL_DOCKER_CONFIG}" && -d "${LOCAL_DOCKER_CONFIG}" ]]; then
    rm -rf "${LOCAL_DOCKER_CONFIG}"
  fi
}
trap cleanup EXIT

# Conditional tag variable for image cache
if [[ -n "${PR_NUMBER}" ]]; then
  export TAG="PR-${PR_NUMBER}"
else
  export TAG="latest"
fi

# Remove possibly previous broken stacks left hanging after an error
docker compose "${COMPOSE_FILES[@]}" down -v --remove-orphans
docker compose "${COMPOSE_FILES[@]}" build "${BUILD_ARGS[@]}"
docker compose "${COMPOSE_FILES[@]}" up -d db migration
docker compose "${COMPOSE_FILES[@]}" logs migration
sleep 5

# Integration tests run as postgres (superuser) since they need full DDL/DML
# access for fixture setup and teardown. The cloudrun role is for runtime only.
TEST_DB_ENV=(
  -e SQLALCHEMY_DATABASE_URI=postgresql://postgres:password@db/postgres
  -e SQLALCHEMY_ASYNC_URI=postgresql+asyncpg://postgres:password@db/postgres
  -e POSTGRESQL_USER=postgres
  -e POSTGRESQL_PASSWORD=password
)

# Now start the integration tests
docker compose "${COMPOSE_FILES[@]}" run --rm "${TEST_DB_ENV[@]}" --entrypoint python api app/db/check_can_connect_to_db.py
docker compose "${COMPOSE_FILES[@]}" run --rm "${TEST_DB_ENV[@]}" --entrypoint bash api /app/scripts/start-tests.sh "$@"
