import hmac
import json
from contextlib import asynccontextmanager

import asyncpg
import niquests
from fastapi import Header, HTTPException, status

from polarsen import env
from polarsen.logs import logs
from polarsen.s3_utils import get_s3_client as _get_s3_client

__all__ = ("connect_pg", "get_client", "get_conn", "get_s3_client", "init_connection", "require_internal_api_token")


async def init_connection(conn: asyncpg.Connection):
    """
    Initialize the PostgreSQL connection with custom type codecs.
    """
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("vector", encoder=json.dumps, decoder=json.loads, schema="public")


PG_POOL: asyncpg.Pool | None = None


@asynccontextmanager
async def connect_pg(pg_url: str, timeout: int = 15):
    """
    Context manager to connect to the PostgreSQL database.

    Args:
        pg_url (str): The PostgreSQL connection URL.
        timeout (int): Connection timeout in seconds.

    Yields:
        asyncpg.Connection: An active database connection.
    """
    global PG_POOL
    pool = await asyncpg.create_pool(pg_url, timeout=timeout, init=init_connection)
    PG_POOL = pool
    try:
        yield pool
    finally:
        await pool.close()


async def get_conn():
    """
    Get a connection from the PostgreSQL connection pool.
    """
    if PG_POOL is None:
        raise RuntimeError("Database connection pool is not initialized. Use connect_pg context manager first.")
    async with PG_POOL.acquire() as conn:
        yield conn


async def get_client():
    async with niquests.AsyncSession() as session:
        yield session


def get_s3_client():
    with _get_s3_client() as s3_client:
        yield s3_client


async def require_internal_api_token(authorization: str | None = Header(None)) -> None:
    """Reject requests without the shared bearer token."""
    try:
        expected_token = env.get_internal_api_token()
    except RuntimeError as exc:
        logs.error("INTERNAL_API_TOKEN is not configured; rejecting internal API request")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal API token is not configured",
        ) from exc

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), expected_token.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid internal API token",
        )
