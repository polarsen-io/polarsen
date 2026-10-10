import asyncio
import datetime as dt
import json
import os
from pathlib import Path
from typing import Final

import niquests
from piou import CommandGroup, Derived, Option

from polarsen.ai.conversations import v2
from polarsen.db import User
from polarsen.db.chat import CHAT_SOURCE_MAPPING, TelegramGroup
from polarsen.logs import logs
from polarsen.pg import get_conn, get_pool
from polarsen.s3_utils import get_s3_client
from polarsen.utils import get_pg_url

from .ingest import process_uploads
from .listener import (
    DEFAULT_EMBEDDING_MODEL,
    process_chat_groups_worker,
    process_chat_worker,
    process_embeddings_worker,
    process_gemini_batch_worker,
    process_stuck_chats_worker,
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
    async with get_conn(pg_url) as conn, niquests.AsyncSession() as session:
        with get_s3_client() as s3_client:
            await process_uploads(
                client=session,
                conn=conn,
                s3_client=s3_client,
                show_progress=show_progress,
            )


DEFAULT_NB_WORKERS: Final[int] = int(os.getenv("NB_WORKERS", "10"))
SLEEP_NO_DATA: Final[int] = int(os.getenv("SLEEP_NO_DATA", "5"))  # seconds to sleep when no data is found


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


DEFAULT_BATCH_POLL_INTERVAL: Final[int] = int(os.getenv("BATCH_POLL_INTERVAL", "300"))  # 5 minutes


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
    batch: bool = Option(False, "--batch", help="Use batch generate content (gemini only)"),
    batch_poll_interval: int = Option(
        DEFAULT_BATCH_POLL_INTERVAL, "--batch-poll-interval", help="Seconds between batch job polling cycles"
    ),
):
    """
    Listen for new chats to be grouped into discussions and process them indefinitely.
    """
    params: v2.ParamsV2 = {
        "model_name": model_name,
    }
    if temperature is not None:
        params["temperature"] = temperature
    if batch:
        params["batch"] = True

    logs.info(f"Starting segmentation listeners with model {model_name!r} and params: {params}")
    logs.info(f"Starting embeddings listeners with model {embedding_model_name!r}")
    if batch:
        logs.info(f"Batch mode enabled, starting Gemini batch worker (poll interval: {batch_poll_interval}s)")

    async with get_pool(pg_url) as pool, asyncio.TaskGroup() as tg:
        # Single worker to reset stuck chats
        tg.create_task(process_stuck_chats_worker(pool, debug=debug, threshold_minutes=stuck_threshold))

        # Gemini batch worker (only needed when batch mode is enabled)
        if batch:
            tg.create_task(
                process_gemini_batch_worker(pool, worker_id=0, sleep_interval=batch_poll_interval, debug=debug)
            )

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


@chat_group.command("process-segmentation")
async def _process_segmentation(
    chat_id: int = Option(..., "--chat-id", help="Chat ID to process"),
    days: int = Option(7, "--days", help="Number of days to process (from today)"),
    pg_url=Derived(get_pg_url),
    model_name: str = Option(v2.SEGMENTATION_MODEL, "--model", help="Model to use for the segmentation"),
    temperature: float | None = Option(None, "--temperature", help="Model temperature"),
    show_progress: bool = Option(False, "--progress", help="Show progress bar"),
    force: bool = Option(False, "--force", help="Force reprocessing of already processed days"),
    batch: bool = Option(False, "--batch", help="Use batch generate content (gemini only)"),
    dry_run: bool = Option(False, "--dry-run", help="Show estimated API calls without processing"),
):
    """
    Process segmentation for a chat for the last X days.
    """
    today = dt.datetime.now(dt.UTC).date()
    date_list = [today - dt.timedelta(days=i) for i in range(days)]

    params: v2.ParamsV2 = {
        "model_name": model_name,
        "days": date_list,
    }
    if temperature is not None:
        params["temperature"] = temperature
    if batch:
        params["batch"] = True

    logs.info(f"Processing segmentation for chat {chat_id} for the last {days} days")
    logs.info(f"Date range: {date_list[-1]} to {date_list[0]}")

    async with get_conn(pg_url) as conn:
        # Get user_id from chat
        chat_record = await conn.fetchrow("SELECT created_by, meta FROM general.chats WHERE id = $1", chat_id)
        if not chat_record:
            logs.error(f"Chat {chat_id} not found")
            return

        user_id = chat_record["created_by"]

        # Get days with messages in the date range (using same filter as get_messages_by_dates)
        days_with_messages = await conn.fetch(
            """
            SELECT DISTINCT sent_at::date as d
            FROM general.chat_messages cm
            WHERE cm.chat_id = $1
              AND cm.sent_at::date = ANY($2)
              AND cm.message <> ''
              AND cm.id IN (SELECT id FROM general.message_replies_view)
            ORDER BY d
            """,
            chat_id,
            date_list,
        )
        days_with_data = {row["d"] for row in days_with_messages}

        # Get already processed days
        if not force:
            processed_days = await conn.fetch(
                """
                SELECT DISTINCT (mg.meta ->> 'day')::date as day
                FROM ai.message_group_chats mgc
                INNER JOIN general.chat_messages cm ON mgc.msg_id = cm.id
                LEFT JOIN ai.message_groups mg ON mg.id = mgc.group_id
                WHERE cm.chat_id = $1
                """,
                chat_id,
            )
            already_processed = {row["day"] for row in processed_days if row["day"]}
            days_to_process = days_with_data - already_processed
        else:
            already_processed = set()
            days_to_process = days_with_data

        nb_calls = len(days_to_process)
        logs.info(f"Days with messages: {len(days_with_data)}, already processed: {len(already_processed)}")
        if batch:
            logs.info(f"Batch mode: 1 API call for {nb_calls} days")
        else:
            logs.info(f"Estimated API calls: {nb_calls}")

        if nb_calls == 0:
            logs.info("No days to process")
            return

        if dry_run:
            logs.info("Dry run - skipping processing")
            return

        # Get API key for the user
        api_keys = await User.get_api_keys(conn, user_id)
        api_key = api_keys.get("gemini") if api_keys else None

        async with niquests.AsyncSession() as session:
            await v2.run_group_messages(
                conn=conn,
                session=session,
                show_progress=show_progress,
                chat_id=chat_id,
                user_id=user_id,
                api_key=api_key,
                force=force,
                **params,
            )

    logs.info(f"Finished processing segmentation for chat {chat_id}")
