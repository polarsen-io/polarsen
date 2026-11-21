import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from polarsen import env

ROOT = Path(__file__).resolve().parents[1]

CREDENTIALS = [
    ("PG_PASSWORD", "get_pg_password"),
    ("S3_ACCESS_KEY_ID", "get_s3_access_key_id"),
    ("S3_SECRET_ACCESS_KEY", "get_s3_secret_access_key"),
]


def _run_without_env(variable: str, code: str) -> subprocess.CompletedProcess[str]:
    _env = os.environ.copy()
    _env.pop(variable, None)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=_env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(("variable", "getter"), CREDENTIALS)
def test_missing_credential_raises(variable: str, getter: str):
    result = _run_without_env(variable, f"import polarsen.env; polarsen.env.{getter}()")

    assert result.returncode != 0
    assert f"{variable} environment variable must be set" in result.stderr


@pytest.mark.parametrize(("variable", "getter"), CREDENTIALS)
def test_getter_reads_the_environment(monkeypatch: pytest.MonkeyPatch, variable: str, getter: str):
    monkeypatch.setenv(variable, "explicit-value")

    assert getattr(env, getter)() == "explicit-value"


def test_pg_dsn_requires_a_password_when_no_dsn_is_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PG_DSN", raising=False)
    monkeypatch.delenv("PG_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="PG_PASSWORD environment variable must be set"):
        env.get_pg_dsn()


def test_pg_dsn_prefers_explicit_dsn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PG_DSN", "postgres://user:pass@db:5432/polarsen")

    assert env.get_pg_dsn() == "postgres://user:pass@db:5432/polarsen"


def test_pg_dsn_encodes_credentials_with_uri_special_characters(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PG_DSN", raising=False)
    monkeypatch.setenv("PG_USER", "user@example.com")
    monkeypatch.setenv("PG_PASSWORD", "p@ss/w:rd#1?")
    monkeypatch.setenv("PG_HOST", "localhost")
    monkeypatch.setenv("PG_DATABASE", "polarsen")

    parsed = urlparse(env.get_pg_dsn())

    assert unquote(parsed.username or "") == "user@example.com"
    assert unquote(parsed.password or "") == "p@ss/w:rd#1?"
    assert parsed.hostname == "localhost"
    assert parsed.path == "/polarsen"
