import json
import re
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, contextmanager
from typing import cast

import asyncpg

__all__ = ("DatabaseConnectionError", "acquire", "get_conn", "get_pool")


class DatabaseConnectionError(Exception):
    """Raised when unable to connect to the database."""


def _sanitize_url(pg_url: str) -> str:
    """Remove password from a PostgreSQL URL for safe logging."""
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", pg_url)


@contextmanager
def _wrap_db_errors(pg_url: str):
    """Context manager to wrap database connection errors with friendly messages."""
    safe_url = _sanitize_url(pg_url)
    try:
        yield
    except socket.gaierror as e:
        raise DatabaseConnectionError(f"Cannot resolve database host for {safe_url}. Is the database running?") from e
    except TimeoutError as e:
        raise DatabaseConnectionError(f"Database connection timed out for {safe_url}") from e
    except OSError as e:
        raise DatabaseConnectionError(f"Cannot connect to database {safe_url}: {e}") from e
    except asyncpg.PostgresError as e:
        raise DatabaseConnectionError(f"Database error for {safe_url}: {e}") from e


async def init_connection(conn: asyncpg.Connection):
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("vector", encoder=json.dumps, decoder=json.loads, schema="public")


@asynccontextmanager
async def get_conn(pg_url: str, *, timeout: int = 5, no_init: bool = False) -> AsyncIterator[asyncpg.Connection]:
    with _wrap_db_errors(pg_url):
        conn = await asyncpg.connect(pg_url, timeout=timeout)
    if not no_init:
        await init_connection(conn)
    try:
        yield conn
    finally:
        await conn.close()


@asynccontextmanager
async def get_pool(pg_url: str) -> AsyncIterator[asyncpg.pool.Pool]:
    with _wrap_db_errors(pg_url):
        async with asyncpg.create_pool(pg_url, init=init_connection) as pool:
            yield pool


@asynccontextmanager
async def acquire(pool: asyncpg.pool.Pool) -> AsyncIterator[asyncpg.Connection]:
    """Acquire a pool connection typed as a plain Connection (asyncpg's proxy type is opaque to pyright)."""
    async with pool.acquire() as conn:
        yield cast(asyncpg.Connection, conn)
