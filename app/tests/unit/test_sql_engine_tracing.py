import os
import subprocess
import sys


def test_all_lazy_sync_engines_record_queries_without_parameter_values(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.tests.util.sql_engine_trace_probe",
            "--sqlite-directory",
            str(tmp_path),
        ],
        env={
            **os.environ,
            "POSTGRESQL_PASSWORD": "unused",
            "SHOPIFY_HMAC_SECRET": "unused",
            "SECRET_KEY": "unused",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "privacy on 2 engines" in result.stdout
