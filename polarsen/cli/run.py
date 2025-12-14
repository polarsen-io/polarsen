import asyncio
import json
import os
from pathlib import Path
from typing import Final

import niquests
from piou import Option, Derived, CommandGroup
from polarsen.logs import logs
from polarsen.ai.conversations import v2
from polarsen.db.chat import CHAT_SOURCE_MAPPING, TelegramGroup
from polarsen.pg import get_conn, get_pool
from polarsen.s3_utils import get_s3_client
from polarsen.utils import get_pg_url
from .ingest import process_uploads
from .listener import (
    process_chat_worker,
    process_chat_groups_worker,
    process_embeddings_worker,
    process_stuck_chats_worker,
    DEFAULT_EMBEDDING_MODEL,
)

__all__ = ("chat_group",)

chat_group = CommandGroup("chat", help="Ingest a chat export file into the database")


@chat_group.command("ingest-file")
async def ingest_export(
    file: Path = Option(..., help="Path to the exported chat file"),
    created_by: int = Option(..., "--user", help="User who uploaded the chat"),
    chat_source: str = Option(..., "--source", help="Chat source name", choices=list(CHAT_SOURCE_MAPPING)),
    show_progress: bool = Option(False, "--progress", help="Show progress bar"),
    pg_url=Derived(get_pg_url),
):
    """
    Ingest a chat export file into the database
    """
    match chat_source:
        case "telegram":
            group = TelegramGroup.load(json.loads(file.read_text()), show_progress=show_progress)
            async with get_conn(pg_url) as conn:
                await group.save(conn=conn, created_by=created_by)
        case _:
            raise ValueError(f"Unsupported chat source {chat_source!r}")


@chat_group.command("process-uploads")
async def _process_uploads(
    show_progress: bool = Option(False, "--progress", help="Show progress bar"),
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
                    tg.create_task(
                        process_chat_worker(pool, s3_client, sleep_no_data=sleep_no_data, worker_id=worker_id)
                    )


@chat_group.command("listen-segmentation")
async def _listen_chat_groups(
    pg_url=Derived(get_pg_url),
    nb_workers: int = Option(DEFAULT_NB_WORKERS, "--workers", help="Number of concurrent workers"),
    sleep_no_data: int = Option(SLEEP_NO_DATA, "--sleep-no-data", help="Seconds to sleep when no data is found"),
    model_name: str = Option(v2.SEGMENTATION_MODEL, "--model", help="Model to use for the segmentation"),
    embedding_model_name: str = Option(
        DEFAULT_EMBEDDING_MODEL, "--embedding-model", help="Model to use for embeddings"
    ),
    temperature: float | None = Option(None, "--temperature", help="[V2] Model temperature"),
    debug: bool = Option(False, "--debug", help="Raise exceptions on errors instead of logging"),
    stuck_threshold: int = Option(30, "--stuck-threshold", help="Minutes before a stuck chat is reset"),
):
    """
    Listen for new chats to be grouped into discussions and process them indefinitely.
    """
    params: v2.ParamsV2 = {
        "model_name": model_name,
    }
    if temperature is not None:
        params["temperature"] = temperature

    logs.info(f"Starting segmentation listeners with model {model_name!r} and params: {params}")
    logs.info(f"Starting embeddings listeners with model {embedding_model_name!r}")

    async with get_pool(pg_url) as pool:
        async with asyncio.TaskGroup() as tg:
            # Single worker to reset stuck chats
            tg.create_task(process_stuck_chats_worker(pool, debug=debug, threshold_minutes=stuck_threshold))

            for worker_id in range(nb_workers):
                tg.create_task(
                    process_chat_groups_worker(pool, sleep_no_data=sleep_no_data, worker_id=worker_id, params=params)
                )
            for worker_id in range(nb_workers):
                tg.create_task(
                    process_embeddings_worker(
                        pool, sleep_no_data=sleep_no_data, worker_id=worker_id, embedding_model=embedding_model_name
                    )
                )
