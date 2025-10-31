import asyncio
import json
import os
from pathlib import Path
from typing import Final

import asyncpg
import botocore.client
import niquests
from piou import Option, Derived, CommandGroup

from polarsen.db.chat import CHAT_SOURCE_MAPPING, TelegramGroup
from polarsen.logs import logs
from polarsen.pg import get_conn, get_pool
from polarsen.s3_utils import get_s3_client
from polarsen.utils import get_pg_url
from .ingest import process_uploads

__all__ = ("chat_group",)

chat_group = CommandGroup("chat", help="Ingest a chat export file into the database")


@chat_group.command("ingest-file")
async def ingest_export(
    file: Path = Option(..., help="Path to the exported chat file"),
    chat_source: str = Option(..., "--source", help="Chat source name", choices=list(CHAT_SOURCE_MAPPING)),
    show_progress: bool = Option(False, "--progress", help="Show progress bar"),
    lang: str = Option("en", "--lang", help="Chat language"),
    pg_url=Derived(get_pg_url),
):
    """
    Ingest a chat export file into the database
    """
    match chat_source:
        case "telegram":
            group = TelegramGroup.load(json.loads(file.read_text()), show_progress=show_progress)
            async with get_conn(pg_url) as conn:
                await group.save(conn=conn, lang=lang)
        case _:
            raise ValueError(f"Unsupported chat source {chat_source!r}")


@chat_group.command("process-uploads")
async def _process_uploads(
    show_progress: bool = Option(False, "--progress", help="Show progress bar"),
    lang: str = Option("en", "--lang", help="Chat language"),
    pg_url=Derived(get_pg_url),
):
    """
    Ingest a chat export file into the database
    """
    async with get_conn(pg_url) as conn:
        async with niquests.AsyncSession() as session:
            with get_s3_client() as s3_client:
                await process_uploads(
                    client=session,
                    conn=conn,
                    s3_client=s3_client,
                    show_progress=show_progress,
                )


DEFAULT_NB_WORKERS: Final[int] = int(os.getenv("NB_WORKERS", 10))
SLEEP_NO_DATA: Final[int] = int(os.getenv("SLEEP_NO_DATA", 5))  # seconds to sleep when no data is found


@chat_group.command("listen-uploads")
async def _listen_uploads(
    pg_url=Derived(get_pg_url),
    nb_workers: int = Option(DEFAULT_NB_WORKERS, "--workers", help="Number of concurrent workers"),
    sleep_no_data: int = Option(SLEEP_NO_DATA, "--sleep-no-data", help="Seconds to sleep when no data is found"),
):
    """
    Listen for new chat uploads and process them indefinitely.
    """
    async with get_pool(pg_url) as pool:
        with get_s3_client() as s3_client:
            async with asyncio.TaskGroup() as tg:
                for worker_id in range(nb_workers):
                    tg.create_task(_worker(pool, s3_client, sleep_no_data=sleep_no_data, worker_id=worker_id))


async def _worker(
    pool: asyncpg.pool.Pool,
    s3_client: botocore.client.BaseClient,
    worker_id: int,
    limit: int = 1,
    sleep_no_data: int = 5,
):
    """
    Worker to process chat uploads.
    This will run indefinitely, processing `limit` uploads at a time.
    If no uploads are found, it will sleep for `sleep_no_data` seconds.
    """
    worker_log = logs.getChild(f"worker-{worker_id}")

    async with pool.acquire() as conn:
        async with niquests.AsyncSession() as session:
            try:
                while True:
                    async with conn.transaction():
                        _processed_items = await process_uploads(
                            client=session, conn=conn, s3_client=s3_client, show_progress=False, limit=limit
                        )
                    if not _processed_items:
                        await asyncio.sleep(sleep_no_data)
            except KeyboardInterrupt:
                worker_log.debug(f"Worker {worker_id} received KeyboardInterrupt, exiting...")
