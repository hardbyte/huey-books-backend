#!/usr/bin/env bash

set -eo pipefail

if [[ -z "${PR_NUMBER}" ]]; then
  echo "PR_NUMBER is required" >&2
  exit 1
fi

if [[ -z "${POSTGRESQL_PASSWORD}" ]]; then
  echo "POSTGRESQL_PASSWORD is required" >&2
  exit 1
fi

POSTGRESQL_APP_USER="${POSTGRESQL_APP_USER:-cloudrun}"
POSTGRESQL_USER="${POSTGRESQL_USER:-postgres}"
PR_DATABASE="wriveted_pr_${PR_NUMBER}"

socket_path="${POSTGRESQL_DATABASE_SOCKET_PATH:-}"
project_id="${GCP_PROJECT_ID:-}"
location="${GCP_LOCATION:-}"
instance_id="${GCP_CLOUD_SQL_INSTANCE_ID:-}"

if [[ -z "${socket_path}" || -z "${project_id}" || -z "${location}" || -z "${instance_id}" ]]; then
  echo "Cloud SQL socket configuration is required (POSTGRESQL_DATABASE_SOCKET_PATH, GCP_PROJECT_ID, GCP_LOCATION, GCP_CLOUD_SQL_INSTANCE_ID)." >&2
  exit 1
fi

export SQLALCHEMY_DATABASE_URI="postgresql+psycopg2://${POSTGRESQL_USER}:${POSTGRESQL_PASSWORD}@/postgres?host=${socket_path}/${project_id}:${location}:${instance_id}"
export PR_DATABASE
export POSTGRESQL_APP_USER

python - <<'PY'
import os
import re
from sqlalchemy import create_engine, text

db_name = os.environ["PR_DATABASE"]
if not re.fullmatch(r"[a-z0-9_]+", db_name):
    raise SystemExit(f"Invalid PR database name: {db_name}")

engine = create_engine(os.environ["SQLALCHEMY_DATABASE_URI"], isolation_level="AUTOCOMMIT")
with engine.connect() as conn:
    exists = conn.execute(
        text("SELECT 1 FROM pg_database WHERE datname=:name"),
        {"name": db_name},
    ).scalar()
    if not exists:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        print(f"Created database {db_name}")
    else:
        print(f"Database {db_name} already exists")
    app_user = os.environ.get("POSTGRESQL_APP_USER", "cloudrun")
    conn.execute(text(f'GRANT ALL PRIVILEGES ON DATABASE "{db_name}" TO "{app_user}"'))
PY

export POSTGRESQL_DATABASE="${PR_DATABASE}"
export SQLALCHEMY_DATABASE_URI="postgresql+psycopg2://${POSTGRESQL_USER}:${POSTGRESQL_PASSWORD}@/${PR_DATABASE}?host=${socket_path}/${project_id}:${location}:${instance_id}"
if [[ "${SKIP_MIGRATIONS:-false}" == "true" ]]; then
  echo "Skipping migrations for ${PR_DATABASE}"
  exit 0
fi

scripts/run-migrations.sh
