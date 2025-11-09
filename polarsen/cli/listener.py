import asyncio

import asyncpg
import botocore.client
import niquests

from polarsen.ai.conversations import v2
from polarsen.logs import logs
from .ingest import process_uploads
from polarsen.common.utils import get_source_from_model
from polarsen.db import DbChat


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
    worker_log = logs.getChild(f"upload-worker-{worker_id}")

    async with pool.acquire() as conn:
        async with niquests.AsyncSession() as session:
            worker_log.info(f"Worker {worker_id}: starting listener")
            try:
                while True:
                    async with conn.transaction():
                        _chat_ids = await process_uploads(
                            client=session, conn=conn, s3_client=s3_client, show_progress=False, limit=limit
                        )
                    if not _chat_ids:
                        await asyncio.sleep(sleep_no_data)
            except KeyboardInterrupt:
                worker_log.debug(f"Worker {worker_id}: received KeyboardInterrupt, exiting...")


async def _get_chats_not_grouped(conn: asyncpg.Connection, limit: int = 1) -> list[asyncpg.Record]:
    records = await conn.fetch(
        """
    with _chats as (
        SELECT mg.id, mg.created_by
        FROM general.chats mg
        WHERE (mg.meta->>'status' IS NULL OR mg.meta->>'status' NOT IN ('processing', 'done'))
        order by mg.id
        FOR UPDATE SKIP LOCKED
        limit $1
    )
    select c.id, u.api_keys
    from _chats c
    left join general.users u on u.id = c.created_by
    """,
        limit,
    )
    return records


async def process_chat_groups_worker(
    pool: asyncpg.pool.Pool,
    worker_id: int,
    params: v2.ParamsV2,
    limit: int = 1,
    sleep_no_data: int = 5,
    timeout: int = 60,
):
    worker_log = logs.getChild(f"group-worker-{worker_id}")
    _model_name = params.get("model_name")
    _source = get_source_from_model(_model_name)
    async with pool.acquire() as conn:
        async with niquests.AsyncSession(timeout=timeout) as session:
            worker_log.debug(f"Worker {worker_id}: Starting work loop")
            _processing_ids: set[int] | None = None
            try:
                while True:
                    async with conn.transaction():
                        _chats = await _get_chats_not_grouped(conn, limit=limit)
                        if not _chats:
                            worker_log.debug(f"Worker {worker_id}: No chats to process, sleeping...")
                            await asyncio.sleep(sleep_no_data)
                            continue
                        _chat_ids = [_chat["id"] for _chat in _chats]
                        _processing_ids = set(_chat_ids)
                        await DbChat.set_is_processing(conn, _chat_ids)

                    for _chat in _chats:
                        _chat_id = _chat["id"]
                        worker_log.debug(f"Worker {worker_id}: Processing chat {_chat_id}")
                        try:
                            api_key = _chat["api_keys"].get(_source)
                            await v2.run_group_messages(
                                conn=conn,
                                session=session,
                                show_progress=False,
                                chat_id=_chat_id,
                                api_key=api_key,
                                **params,
                            )
                        except Exception as e:
                            worker_log.error(f"Worker {worker_id}: Error processing chat {_chat_id}: {e}")
                            await DbChat.set_processing_error(conn, [_chat_id], message=str(e))
                        else:
                            worker_log.debug(f"Worker {worker_id}: Successfully processed chat {_chat_id}")
                            await DbChat.set_processing_done(conn, [_chat_id])
                        finally:
                            _processing_ids.remove(_chat_id)
            except Exception:
                if _processing_ids is not None:
                    worker_log.debug(f"Worker {worker_id}: Caught an exception, resetting {len(_processing_ids)}")
                    await DbChat.reset_processing(conn, list(_processing_ids))
                raise
