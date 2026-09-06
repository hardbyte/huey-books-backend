#!/usr/bin/env bash
set -euo pipefail

if [[ ! "${POSTGRESQL_DATABASE:-}" =~ ^wriveted_pr_[0-9]+$ ]]; then
  echo "A PR database is required for preview role setup" >&2
  exit 1
fi

python - <<'PY'
import os
from sqlalchemy.engine import make_url

if make_url(os.environ["SQLALCHEMY_DATABASE_URI"]).database != os.environ["POSTGRESQL_DATABASE"]:
    raise SystemExit("Role setup connection does not target the PR database")
PY

pgroles_binary="${PGROLES_BINARY:-}"
if [[ -z "${pgroles_binary}" ]]; then
  artifact_dir="$(mktemp -d)"
  trap 'rm -rf "${artifact_dir}"' EXIT
  archive="${artifact_dir}/pgroles.tar.gz"
  curl --fail --silent --show-error --location \
    https://github.com/thepartly/pgroles/releases/download/v0.1.5/pgroles-v0.1.5-x86_64-unknown-linux-musl.tar.gz \
    --output "${archive}"
  printf '%s  %s\n' 4a2056dde8bc32dae29c143601bd3f18cb07427ecf1bf07bbad4004972fe73c4 "${archive}" | sha256sum --check
  tar -xzf "${archive}" -C "${artifact_dir}" --strip-components=1 \
    pgroles-v0.1.5-x86_64-unknown-linux-musl/pgroles
  pgroles_binary="${artifact_dir}/pgroles"
fi

export DATABASE_URL="${SQLALCHEMY_DATABASE_URI/postgresql+psycopg2:/postgresql:}"
"${pgroles_binary}" apply --file pgroles.yaml
