#!/bin/bash

set -eo pipefail

CLOUD_SQL_INSTANCE="${CLOUD_SQL_INSTANCE:-wriveted-api:australia-southeast1:wriveted}"
POSTGRES_PORT="5432"

proxy_connection_cleanup() {
  echo "cleaning up cloud_sql_proxy connection"
  kill "$(jobs -p)"
}
trap proxy_connection_cleanup EXIT SIGTERM SIGINT SIGQUIT

echo "Downloading cloud_sql_proxy"
curl -s "https://dl.google.com/cloudsql/cloud_sql_proxy.linux.amd64" -o "${HOME}/cloud_sql_proxy"
chmod +x "${HOME}/cloud_sql_proxy"
"${HOME}/cloud_sql_proxy" -instances="${CLOUD_SQL_INSTANCE}=tcp:localhost:${POSTGRES_PORT}" &

export SQLALCHEMY_DATABASE_URI="postgresql://postgres:${POSTGRESQL_PASSWORD}@localhost/postgres"
python -m scripts.reconcile_school_billing --apply
