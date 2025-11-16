import asyncio

import asyncpg
import botocore.client
import niquests

from polarsen.ai.conversations import v2
from polarsen.common.utils import get_source_from_model, AISource
from polarsen.db import DbChat
from polarsen.logs import logs, WorkerLoggerAdapter
from .ingest import process_uploads

__all__ = ("process_chat_worker", "process_chat_groups_worker")


async def process_chat_worker(
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
    worker_log = WorkerLoggerAdapter(logs, {"worker_id": worker_id})

    async with pool.acquire() as conn:
        async with niquests.AsyncSession() as session:
            worker_log.info(f"Worker {worker_id}: starting listener")
            try:
                while True:
                    async with conn.transaction():
                        _chat_ids = await process_uploads(
                            client=session,
                            conn=conn,
                            s3_client=s3_client,
                            show_progress=False,
                            limit=limit,
                            logger=worker_log,
                        )
                    if not _chat_ids:
                        await asyncio.sleep(sleep_no_data)
            except KeyboardInterrupt:
                worker_log.debug("Received KeyboardInterrupt, exiting...")


async def _get_chats_not_grouped(
    conn: asyncpg.Connection, source: AISource, limit: int = 1, only_with_keys: bool = True
) -> list[asyncpg.Record]:
    """
    Get chats that are not yet grouped - with status not in ('processing', 'done') - and lock them for processing.
    If only_with_keys is True, only return chats where the user has an API key for the given source.
    """
    records = await conn.fetch(
        """
        SELECT mg.id, mg.created_by, u.api_keys->>$2 as api_key, u.id as user_id
        FROM general.chats mg
        LEFT JOIN general.users u ON u.id = mg.created_by
        WHERE (mg.meta->>'status' IS NULL OR mg.meta->>'status' NOT IN ('processing', 'done'))
          AND CASE WHEN $3 THEN u.api_keys->>$2 IS NOT NULL ELSE TRUE END
        ORDER BY mg.id
        FOR UPDATE OF mg SKIP LOCKED
        LIMIT $1
        """,
        limit,
        source,
        only_with_keys,
    )
    return records


async def process_chat_groups_worker(
    pool: asyncpg.pool.Pool,
    params: v2.ParamsV2,
    worker_id: int = 0,  # Id of the worker for logs
    limit: int = 1,
    sleep_no_data: int = 5,  # Time to sleep when no data is found
    timeout: int = 60,
    run_forever: bool = True,  # Should break or not after 1 pass
    only_with_keys: bool = True,  # Only process chats where the user has an API key
):
    """
    Worker to process chat grouping into discussions.
    This will run indefinitely (unless `run_forever` is False), processing `limit` chats at a time.
    If no chats are found, it will sleep for `sleep_no_data` seconds.
    Chats being processed will be marked as 'processing' to avoid double processing by other worker.
    When `only_with_keys` is True, only chats where the user has an API key for the model source will be processed.
    This is useful when no global API key is set.
    """
    worker_log = WorkerLoggerAdapter(logs, {"worker_id": worker_id})
    _model_name = params.get("model_name")
    _source = get_source_from_model(_model_name)
    async with pool.acquire() as conn:
        async with niquests.AsyncSession(timeout=timeout) as session:
            worker_log.debug("Starting work loop")
            _processing_ids: set[int] | None = None
            try:
                while True:
                    async with conn.transaction():
                        _chats = await _get_chats_not_grouped(
                            conn, source=_source, limit=limit, only_with_keys=only_with_keys
                        )
                        if not _chats:
                            worker_log.debug("No chats to process, sleeping...")
                            await asyncio.sleep(sleep_no_data)
                            if not run_forever:
                                break
                            continue
                        _chat_ids = [_chat["id"] for _chat in _chats]
                        _processing_ids = set(_chat_ids)
                        await DbChat.set_is_processing(conn, _chat_ids)
                    for _chat in _chats:
                        _chat_id = _chat["id"]
                        worker_log.debug(f"Processing chat {_chat_id}")
                        try:
                            await v2.run_group_messages(
                                conn=conn,
                                session=session,
                                show_progress=False,
                                chat_id=_chat_id,
                                api_key=_chat["api_key"],
                                user_id=_chat["user_id"],
                                **params,
                            )
                        except Exception as e:
                            worker_log.error(f"Error processing chat {_chat_id}: {e}")
                            await DbChat.set_processing_error(conn, [_chat_id], message=str(e))
                            if not run_forever:
                                raise e
                        else:
                            worker_log.debug(f"Successfully processed chat {_chat_id}")
                            await DbChat.set_processing_done(conn, [_chat_id])
                        finally:
                            _processing_ids.remove(_chat_id)
            except Exception:
                if _processing_ids is not None:
                    worker_log.debug(f"Caught an exception, resetting {len(_processing_ids)}")
                    await DbChat.reset_processing(conn, list(_processing_ids))
                raise
