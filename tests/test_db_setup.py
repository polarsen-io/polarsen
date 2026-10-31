import asyncio
import os
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from unittest import mock

from piou import Password

from polarsen.cli.db import _setup_db


class _FakeConn:
    async def fetchval(self, *_args):
        return False

    async def execute(self, _sql):
        return None


@asynccontextmanager
async def _fake_get_conn(*_args, **_kwargs):
    yield _FakeConn()


def _run_setup(tmp_path: Path, pg_database: str):
    args_path = tmp_path / "createdb-args.txt"
    marker_path = tmp_path / "injected"

    createdb = tmp_path / "createdb"
    createdb.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CREATEDB_ARGS_FILE"\n')
    createdb.chmod(0o755)

    env = {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "CREATEDB_ARGS_FILE": str(args_path),
    }
    with (
        mock.patch.dict(os.environ, env),
        mock.patch("polarsen.cli.db.get_conn", _fake_get_conn),
        mock.patch("polarsen.cli.db.set_pg_env", return_value=nullcontext()),
        mock.patch("polarsen.cli.db.SQL_DIR", tmp_path),
    ):
        asyncio.run(
            _setup_db(
                pg_host="localhost",
                pg_port=5432,
                pg_user="postgres",
                pg_password=Password("password"),
                pg_database=pg_database,
                no_data=True,
            )
        )
    return args_path, marker_path


def test_createdb_receives_database_name_as_argv(tmp_path: Path):
    args_path, _ = _run_setup(tmp_path, "polarsen_test")

    assert args_path.read_text().splitlines() == ["polarsen_test"]


def test_createdb_does_not_execute_injected_shell_command(tmp_path: Path):
    database_name = f"x; touch {tmp_path / 'injected'}"

    args_path, marker_path = _run_setup(tmp_path, database_name)

    assert not marker_path.exists()
    assert args_path.read_text().splitlines() == [database_name]
