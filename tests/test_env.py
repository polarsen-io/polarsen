import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_without_token(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("INTERNAL_API_TOKEN", None)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_cli_and_listener_import_without_internal_api_token():
    result = _run_without_token("import polarsen.env; import polarsen.cli.db; import polarsen.cli.listener")
    assert result.returncode == 0, result.stderr
