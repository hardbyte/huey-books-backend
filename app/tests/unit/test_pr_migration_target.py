import os
import subprocess
from pathlib import Path


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
