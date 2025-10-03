import os

import psycopg
import pytest
from minio import Minio
import asyncio
import asyncpg

os.environ["PG_DATABASE"] = os.getenv("PG_DATABASE", "polarsen_test")
os.environ["PG_HOST"] = os.getenv("PG_HOST", "localhost")
os.environ["PG_USER"] = os.getenv("PG_USER", "postgres")
os.environ["PG_PASSWORD"] = os.getenv("PG_PASSWORD", "password")
os.environ["_PG_URL"] = "postgresql://{PG_USER}:${PG_PASSWORD}@{PG_HOST}:5432/{PG_DATABASE}".format(**os.environ)

os.environ["S3_ENDPOINT"] = os.getenv("S3_ENDPOINT", "http://localhost:9000")
os.environ["S3_ACCESS_KEY_ID"] = os.getenv("S3_ACCESS_KEY_ID", "minioadmin")
os.environ["S3_SECRET_ACCESS_KEY"] = os.getenv("S3_SECRET_ACCESS_KEY", "minioadmin")


@pytest.fixture(scope="session")
def engine():
    conn: psycopg.Connection = psycopg.connect(
        dbname=os.environ["PG_DATABASE"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
        host=os.environ["PG_HOST"],
        port=5432,
    )
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def minio_client():
    client = Minio(
        endpoint=os.environ["S3_ENDPOINT"].lstrip("http://"),
        access_key=os.environ["S3_ACCESS_KEY_ID"],
        secret_key=os.environ["S3_SECRET_ACCESS_KEY"],
        secure=False,
    )
    yield client


@pytest.fixture(scope="session")
def loop():
    _loop = asyncio.new_event_loop()
    try:
        yield _loop
    finally:
        _loop.close()


@pytest.fixture(scope="session")
def aengine(loop):
    conn = loop.run_until_complete(
        asyncpg.connect(
            database=os.environ["PG_DATABASE"],
            user=os.environ["PG_USER"],
            password=os.environ["PG_PASSWORD"],
            host=os.environ["PG_HOST"],
            port=5432,
        )
    )
    try:
        yield conn
    finally:
        loop.run_until_complete(conn.close())
