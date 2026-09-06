import os
import subprocess
from pathlib import Path

import pytest


def test_pr_migrations_use_the_pr_database(tmp_path):
    script = Path(__file__).resolve().parents[3] / "scripts/run-pr-migrations.sh"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    python_stub = binaries / "python"
    python_stub.write_text("#!/bin/sh\nexit 0\n")
    python_stub.chmod(0o755)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    migrations = scripts / "run-migrations.sh"
    migrations.write_text(
        '#!/bin/sh\n[ "$SQLALCHEMY_DATABASE_URI" = '
        '"postgresql+psycopg2://postgres:test@/wriveted_pr_746?host=/cloudsql/test:region:instance" ]\n'
    )
    migrations.chmod(0o755)
    role_setup = scripts / "apply-pr-database-roles.sh"
    role_setup.write_text(migrations.read_text())
    result = subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "PR_NUMBER": "746",
            "POSTGRESQL_USER": "postgres",
            "POSTGRESQL_PASSWORD": "test",
            "POSTGRESQL_DATABASE_SOCKET_PATH": "/cloudsql",
            "GCP_PROJECT_ID": "test",
            "GCP_LOCATION": "region",
            "GCP_CLOUD_SQL_INSTANCE_ID": "instance",
            "SKIP_MIGRATIONS": "false",
        },
    )
    assert result.returncode == 0, (
        "Migration command did not receive the PR database URI"
    )


@pytest.mark.parametrize("database", ["postgres", "wriveted", "", "wriveted_pr_invalid"])
def test_preview_roles_reject_non_preview_database(database):
    script = Path(__file__).resolve().parents[3] / "scripts/apply-pr-database-roles.sh"
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env={**os.environ, "POSTGRESQL_DATABASE": database},
    )
    assert result.returncode == 1
    assert "A PR database is required" in result.stderr


def test_preview_roles_normalize_socket_connection_url(tmp_path):
    script = Path(__file__).resolve().parents[3] / "scripts/apply-pr-database-roles.sh"
    pgroles = tmp_path / "pgroles"
    pgroles.write_text('#!/bin/sh\nprintf "%s" "$DATABASE_URL"\n')
    pgroles.chmod(0o755)
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "POSTGRESQL_DATABASE": "wriveted_pr_746",
            "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg2://postgres:test@/wriveted_pr_746?host=/cloudsql/project:region:instance",
            "PGROLES_BINARY": str(pgroles),
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "postgresql://postgres:test@localhost/wriveted_pr_746"
        "?host=%2Fcloudsql%2Fproject%3Aregion%3Ainstance"
    )
