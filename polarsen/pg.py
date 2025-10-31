import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

__all__ = ("get_conn", "get_pool")


async def init_connection(conn: asyncpg.Connection):
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("vector", encoder=json.dumps, decoder=json.loads, schema="public")


@asynccontextmanager
async def get_conn(pg_url: str, *, timeout: int = 5, no_init: bool = False) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(pg_url, timeout=timeout)
    if not no_init:
        await init_connection(conn)
    try:
        yield conn
    finally:
        await conn.close()


@asynccontextmanager
async def get_pool(pg_url: str) -> AsyncIterator[asyncpg.pool.Pool]:
    async with asyncpg.create_pool(pg_url, init=init_connection) as pool:
        yield pool
