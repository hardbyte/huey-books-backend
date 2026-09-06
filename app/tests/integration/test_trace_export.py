import subprocess
import sys


def test_instrumented_pool_checkout_does_not_wait_for_export():
    result = subprocess.run(
        [sys.executable, "-m", "app.tests.util.trace_export_probe"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "70 exported spans" in result.stdout
