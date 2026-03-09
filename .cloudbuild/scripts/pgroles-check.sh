#!/bin/bash
# Check database role drift against pgroles manifest (dry-run only).
# Designed to run in Cloud Build after migrations, with cloud_sql_proxy
# already available or started by this script.

set -eo pipefail

PGROLES_VERSION="${PGROLES_VERSION:-0.1.5}"
CLOUD_SQL_INSTANCE="${CLOUD_SQL_INSTANCE:-wriveted-api:australia-southeast1:wriveted}"
POSTGRES_PORT="5432"

# Download pgroles if not present
if [[ ! -x ./pgroles ]]; then
  echo "Downloading pgroles v${PGROLES_VERSION}"
  curl -sL "https://github.com/hardbyte/pgroles/releases/download/v${PGROLES_VERSION}/pgroles-v${PGROLES_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
    | tar xz --strip-components=1
  chmod +x pgroles
fi

./pgroles --version

# Start cloud_sql_proxy if DATABASE_URL is not already set
if [[ -z "${DATABASE_URL}" ]]; then
  proxy_connection_cleanup() {
    echo "Cleaning up cloud_sql_proxy connection"
    kill "$(jobs -p)" 2>/dev/null || true
  }
  trap proxy_connection_cleanup EXIT SIGTERM SIGINT SIGQUIT

  echo "Downloading cloud_sql_proxy"
  curl -s "https://dl.google.com/cloudsql/cloud_sql_proxy.linux.amd64" -o "${HOME}/cloud_sql_proxy"
  chmod +x "${HOME}/cloud_sql_proxy"
  "${HOME}/cloud_sql_proxy" -instances="${CLOUD_SQL_INSTANCE}=tcp:localhost:${POSTGRES_PORT}" &
  sleep 2

  DATABASE_URL="postgresql://postgres:${POSTGRESQL_PASSWORD}@localhost:${POSTGRES_PORT}/${POSTGRESQL_DATABASE:-postgres}"
fi

echo "=== pgroles manifest validation ==="
./pgroles validate --file pgroles.yaml

echo ""
echo "=== pgroles role drift check (dry run) ==="
./pgroles diff \
  --database-url "${DATABASE_URL}" \
  --file pgroles.yaml \
  --format summary \
  --no-exit-code

echo ""
./pgroles diff \
  --database-url "${DATABASE_URL}" \
  --file pgroles.yaml \
  --format sql \
  --no-exit-code
