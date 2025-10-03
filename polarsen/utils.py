import hashlib
from typing import AsyncIterator

from piou import Option, Password

from .env import (
    PG_HOST,
    PG_PORT,
    PG_USER,
    PG_PASSWORD,
    PG_DATABASE,
)

PgHost = Option(PG_HOST, "--host", help="PG Host")
PgPort = Option(PG_PORT, "--port", help="PG Port")
PgUser = Option(PG_USER, "--user", help="PG User")
PgPassword = Option(PG_PASSWORD, "-p", help="PG Password")
PgDatabase = Option(PG_DATABASE, "--db", help="PG Database")


def get_pg_url(
    pg_host: str = PgHost,
    pg_port: int = PgPort,
    pg_user: str = PgUser,
    pg_password: Password = PgPassword,
    pg_database: str = PgDatabase,
) -> str:
    return f"postgresql://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_database}"


def compute_md5():
    """Compute MD5 hash incrementally."""
    file_hash = hashlib.md5()

    def _update_hash(chunk: bytes):
        file_hash.update(chunk)

    def _get_hash():
        return file_hash.hexdigest()

    return _get_hash, _update_hash


async def get_stream_chunk(data_stream: AsyncIterator[bytes], min_part_size: int) -> AsyncIterator[bytes]:
    """Yield chunks of at least min_part_size from an async byte stream."""
    buffer: bytes = b""
    buffer_size = 0

    async for chunk in data_stream:
        if not chunk:
            continue
        buffer += chunk
        buffer_size += len(chunk)

        # Yield chunks of min_part_size while we have enough data for at least 2 chunks
        while buffer_size >= min_part_size * 2:
            yield buffer[:min_part_size]
            buffer = buffer[min_part_size:]
            buffer_size -= min_part_size

    # Handle the final chunk(s)
    if buffer_size >= min_part_size:
        # If we have enough for exactly one chunk, yield it
        if buffer_size < min_part_size * 2:
            yield buffer
        else:
            # We have enough for more than one chunk, split evenly
            mid_point = buffer_size // 2
            yield buffer[:mid_point]
            yield buffer[mid_point:]
