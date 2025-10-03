import os
import time

from piou import CommandGroup, Password
from tracktolib.pg import insert_many
from tracktolib.utils import exec_cmd

from polarsen.env import SQL_DIR
from polarsen.logs import logs
from polarsen.pg import get_conn
from polarsen.utils import PgHost, PgPort, PgUser, PgPassword, PgDatabase
from .utils import set_pg_env

db_group = CommandGroup("db", help="Database commands")

CHAT_TYPES = [
    {"id": 0, "name": "Telegram", "internal_code": "telegram"},
]


@db_group.command("setup", help="Generate embeddings for messages in discussions")
async def run_gen_embeddings(
    pg_host: str = PgHost,
    pg_port: int = PgPort,
    pg_user: str = PgUser,
    pg_password: Password = PgPassword,
    pg_database: str = PgDatabase,
):
    os.environ["SHELL"] = "/bin/sh"

    _pg_url_no_db = f"postgresql://{pg_user}:{pg_password}@{pg_host}:{pg_port}"

    start = time.time()

    # Create database if not exists
    async with get_conn(f"{_pg_url_no_db}/postgres", no_init=True) as conn:
        db_exists = await conn.fetchval("SELECT exists(select 1 FROM pg_database WHERE datname = $1)", pg_database)
    if not db_exists:
        logs.debug(f"Database {pg_database!r} does not exist, creating...")
        with set_pg_env(f"{_pg_url_no_db}/postgres"):
            logs.debug(f"Creating database {pg_database!r}...")
            exec_cmd("createdb {}".format(pg_database))

    # Create extensions and tables

    async with get_conn(f"{_pg_url_no_db}/{pg_database}", no_init=True) as conn:
        for file in sorted(SQL_DIR.glob("*.sql")):
            logs.debug(f"Executing {file}...")
            sql = file.read_text()
            await conn.execute(sql)

        await insert_many(conn, "general.chat_types", CHAT_TYPES, on_conflict="ON CONFLICT DO NOTHING")

    logs.info(f"Database setup completed, took {time.time() - start:.2f}s")
    # Insert initial data if needed
