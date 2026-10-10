import asyncio
import os
from pathlib import Path

import asyncpg
import botocore.client
import msgspec
import niquests
from google.genai.types import JobState

from polarsen.ai.conversations import v2
from polarsen.ai.embeddings import DEFAULT_EMBEDDING_MODEL, EmbeddingGroup, gen_groups_embeddings
from polarsen.common.models import gemini
from polarsen.common.utils import AISource, get_source_from_model
from polarsen.db import BatchJob, DbChat, MessageGroup, User
from polarsen.db.crypto import decrypt_secret
from polarsen.logs import logs, set_worker_logger
from polarsen.pg import acquire

from .ingest import process_uploads

__all__ = (
    "DEFAULT_EMBEDDING_MODEL",
    "process_chat_groups_worker",
    "process_chat_worker",
    "process_embeddings_worker",
    "process_gemini_batch_worker",
    "process_stuck_chats_worker",
)

# Health file touched on each loop iteration to signal liveness (e.g., for container health checks)
HEALTH_FILE = Path(os.environ.get("HEALTH_FILE", "/tmp/health"))


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
    set_worker_logger(worker_id, "UploadWorker")

    async with acquire(pool) as conn, niquests.AsyncSession() as session:
        logs.info(f"Worker {worker_id}: starting listener")
        try:
            while True:
                HEALTH_FILE.touch()
                async with conn.transaction():
                    _chat_ids = await process_uploads(
                        client=session,
                        conn=conn,
                        s3_client=s3_client,
                        show_progress=False,
                        limit=limit,
                    )
                if not _chat_ids:
                    await asyncio.sleep(sleep_no_data)
        except KeyboardInterrupt:
            logs.debug("Received KeyboardInterrupt, exiting...")


STUCK_PROCESSING_THRESHOLD_MINUTES = 30


async def _reset_stuck_processing_chats(
    conn: asyncpg.Connection, threshold_minutes: int = STUCK_PROCESSING_THRESHOLD_MINUTES
) -> int:
    """
    Reset chats that have been stuck in 'processing' status for longer than threshold_minutes.
    Also resets chats without a processing_started_at timestamp (legacy stuck chats).
    Returns the number of chats reset.
    """
    result = await conn.execute(
        """
        UPDATE general.chats
        SET meta = meta - 'status'
        WHERE meta ->> 'status' = 'processing'
          AND (
              meta ->> 'processing_started_at' IS NULL
              OR (meta ->> 'processing_started_at')::timestamptz < NOW() - ($1 || ' minutes')::interval
          )
        """,
        str(threshold_minutes),
    )
    # Result is like "UPDATE N"
    count = int(result.split()[-1]) if result else 0
    return count


async def process_stuck_chats_worker(
    pool: asyncpg.pool.Pool,
    worker_id: int = 0,
    check_interval: int = 60,  # Check every minute
    threshold_minutes: int = STUCK_PROCESSING_THRESHOLD_MINUTES,
    debug: bool = False,
):
    """
    Worker to periodically reset chats stuck in 'processing' status.
    Runs independently to avoid blocking the main processing workers.
    """
    set_worker_logger(worker_id, "StuckChatsWorker")
    logs.info(f"Starting stuck chats worker (threshold: {threshold_minutes}min, interval: {check_interval}s)")

    async with acquire(pool) as conn:
        while True:
            try:
                reset_count = await _reset_stuck_processing_chats(conn, threshold_minutes)
                if reset_count > 0:
                    logs.info(f"Reset {reset_count} stuck chat(s)")
            except Exception as e:
                logs.error(f"Error resetting stuck chats: {e}")
                if debug:
                    raise

            await asyncio.sleep(check_interval)


async def _get_chats_not_grouped(
    conn: asyncpg.Connection, source: AISource, limit: int = 1, only_with_keys: bool = True
) -> list[dict]:
    """
    Get chats that are not yet grouped - with status not in ('processing', 'done') - and lock them for processing.
    If only_with_keys is True, only return chats where the user has an API key for the given source.
    """
    records = await conn.fetch(
        """
        SELECT mg.id, mg.created_by, u.api_keys ->> $2 AS api_key, u.id AS user_id
        FROM general.chats mg
                 LEFT JOIN general.users u ON u.id = mg.created_by
        WHERE (mg.meta ->> 'status' IS NULL OR mg.meta ->> 'status' NOT IN ('processing', 'done'))
          AND CASE WHEN $3 THEN NULLIF(u.api_keys ->> $2, '') IS NOT NULL ELSE TRUE END
        ORDER BY mg.id
            FOR UPDATE OF mg SKIP LOCKED
        LIMIT $1
        """,
        limit,
        source,
        only_with_keys,
    )
    return [{**r, "api_key": decrypt_secret(r["api_key"]) if r["api_key"] else None} for r in records]


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
    set_worker_logger(worker_id, "ChatGroupWorker")
    _model_name = params.get("model_name")
    _source = get_source_from_model(_model_name)
    if params.get("batch", False) and _source != "gemini":
        logs.warning("Batch processing is only supported for Gemini models. Disabling batch mode.")
        params["batch"] = False

    async with acquire(pool) as conn, niquests.AsyncSession(timeout=timeout) as session:
        logs.debug("Starting work loop")
        _processing_ids: set[int] | None = None
        try:
            while True:
                HEALTH_FILE.touch()
                async with conn.transaction():
                    _chats = await _get_chats_not_grouped(
                        conn, source=_source, limit=limit, only_with_keys=only_with_keys
                    )
                    if not _chats:
                        logs.debug("No chats to process, sleeping...")
                        await asyncio.sleep(sleep_no_data)
                        if not run_forever:
                            break
                        continue
                    _chat_ids = [_chat["id"] for _chat in _chats]
                    _processing_ids = set(_chat_ids)
                    await DbChat.set_is_processing(conn, _chat_ids)
                for _chat in _chats:
                    _chat_id = _chat["id"]
                    logs.debug(f"Processing chat {_chat_id}")
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
                        logs.error(f"Error processing chat {_chat_id}: {e}")
                        await DbChat.set_processing_error(conn, [_chat_id], message=str(e))
                        if not run_forever:
                            raise
                    else:
                        logs.debug(f"Successfully processed chat {_chat_id}")
                        await DbChat.set_processing_done(conn, [_chat_id])
                    finally:
                        _processing_ids.remove(_chat_id)
        except Exception:
            if _processing_ids is not None:
                logs.debug(f"Caught an exception, resetting {len(_processing_ids)}")
                await DbChat.reset_processing(conn, list(_processing_ids))
            raise


async def _get_groups_not_embedded(conn: asyncpg.Connection, source: AISource, limit: int = 1) -> list[EmbeddingGroup]:
    """
    Get chat groups that do not have embeddings yet - i.e., no relation exists in ai.mistral_group_embeddings -
    and lock them for processing. Only returns groups where the user has an API key for the given source.
    """
    records = await conn.fetch(
        """
        SELECT mg.id, mg.title, mg.summary, msg.messages, c.created_by AS user_id,
               u.api_keys ->> $2 AS api_key
        FROM ai.message_groups mg
        LEFT JOIN ai.mistral_group_embeddings cge ON cge.group_id = mg.id
        LEFT JOIN general.chats c ON c.id = mg.chat_id
        LEFT JOIN general.users u ON u.id = c.created_by
        LEFT JOIN LATERAL (
            SELECT ARRAY_AGG(cm.message ORDER BY cm.sent_at) AS messages
            FROM ai.message_group_chats m
                     LEFT JOIN general.chat_messages cm ON cm.id = m.msg_id
            WHERE m.group_id = mg.id
              AND cm.message <> ''
            ) msg ON TRUE
              -- Ignoring groups that are currently being processed
        WHERE COALESCE(mg.meta ->> 'embeddings_status' <> 'processing', TRUE)
             -- Only groups without embeddings yet
             AND cge.id IS NULL
             -- Only groups where the user has an API key for the source
             AND NULLIF(u.api_keys ->> $2, '') IS NOT NULL
        ORDER BY mg.id
            FOR UPDATE OF mg SKIP LOCKED
        LIMIT $1
        """,
        limit,
        source,
    )
    return [
        msgspec.convert(
            {**row, "api_key": decrypt_secret(row["api_key"]) if row["api_key"] else None},
            EmbeddingGroup,
        )
        for row in records
    ]


async def process_embeddings_worker(
    pool: asyncpg.pool.Pool,
    worker_id: int = 0,
    chunk_size: int = 100,
    sleep_no_data: int = 5,
    timeout: int = 60,
    run_forever: bool = True,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
):
    set_worker_logger(worker_id, "EmbeddingWorker")
    _source = get_source_from_model(embedding_model)
    async with acquire(pool) as conn, niquests.AsyncSession(timeout=timeout) as session:
        logs.info("Starting embedding work loop")
        _processing_ids: set[int] | None = None
        try:
            while True:
                HEALTH_FILE.touch()
                async with conn.transaction():
                    _groups = await _get_groups_not_embedded(conn, source=_source, limit=chunk_size)
                    if not _groups:
                        logs.debug("No groups found, sleeping...")
                        await asyncio.sleep(sleep_no_data)
                        if not run_forever:
                            break
                        continue
                    _group_ids = [_group["id"] for _group in _groups]
                    _processing_ids = set(_group_ids)
                    await MessageGroup.set_is_processing(conn, _group_ids)
                try:
                    await gen_groups_embeddings(conn, session, groups=_groups, model_name=embedding_model)
                except Exception as e:
                    logs.error(f"Error processing {len(_groups)} groups: {e}")
                    await MessageGroup.set_processing_error(conn, _group_ids, message=str(e))
                    _processing_ids = None  # Clear to prevent reset in outer except block
                    if not run_forever:
                        raise
                else:
                    logs.debug(f"Successfully processed {len(_groups)} groups")
                    await MessageGroup.set_processing_done(conn, _group_ids)

                if not run_forever:
                    break

        except Exception:
            if _processing_ids is not None:
                logs.debug(f"Caught an exception, resetting {len(_processing_ids)}")
                await MessageGroup.reset_processing(conn, list(_processing_ids))
            raise


async def process_gemini_batch_worker(
    pool: asyncpg.pool.Pool,
    worker_id: int = 0,
    sleep_interval: int = 300,  # 5 minutes
    timeout: int = 60,
    run_forever: bool = True,
    debug: bool = False,
):
    """
    Worker to poll Gemini batch jobs for completion and process results.

    This worker:
    1. Fetches pending/running Gemini batch jobs from the database
    2. Polls the Gemini API for each job's status (using the user's API key)
    3. Processes completed jobs using v2.process_gemini_batch_result
    4. Marks jobs as completed or failed in the database

    Args:
        pool: Database connection pool
        worker_id: Worker ID for logging
        sleep_interval: Seconds to sleep between polling cycles (default: 5 minutes)
        timeout: HTTP request timeout in seconds
        run_forever: If False, exit after one polling cycle
        debug: If True, re-raise exceptions for debugging
    """
    set_worker_logger(worker_id, "GeminiBatchWorker")
    logs.info(f"Starting Gemini batch worker (poll interval: {sleep_interval}s)")

    async with acquire(pool) as conn, niquests.AsyncSession(timeout=timeout) as session:
        while True:
            HEALTH_FILE.touch()

            try:
                pending_jobs = await BatchJob.get_pending_gemini_jobs(conn)
                if pending_jobs:
                    logs.info(f"Found {len(pending_jobs)} pending Gemini batch job(s)")

                for job_record in pending_jobs:
                    batch_job = BatchJob.from_record(job_record)
                    job_name = batch_job.gemini_job_name

                    if not job_name:
                        logs.warning(f"Batch job {batch_job._id} has no gemini_job_name, skipping")
                        continue

                    # Get user's API key
                    api_keys = await User.get_api_keys(conn, batch_job.user_id)
                    api_key = api_keys.get("gemini") if api_keys else None
                    if not api_key:
                        logs.warning(f"User {batch_job.user_id} has no Gemini API key, skipping job {job_name}")
                        continue

                    # Set the user's API key for this request
                    gemini.set_headers(session, api_key)

                    logs.debug(f"Checking status of Gemini job: {job_name}")

                    try:
                        gemini_job = await gemini.get_batch_job(session, job_name)
                    except Exception as e:
                        logs.error(f"Error fetching Gemini job {job_name}: {e}")
                        continue

                    # Check if job is still running
                    if gemini_job.state in (
                        JobState.JOB_STATE_PENDING,
                        JobState.JOB_STATE_QUEUED,
                        JobState.JOB_STATE_RUNNING,
                    ):
                        logs.debug(f"Job {job_name} still in progress: {gemini_job.state}")
                        # Update to running if it was pending
                        if batch_job.status == "pending":
                            await batch_job.start(conn)
                        continue

                    # Job is done
                    if gemini_job.state == JobState.JOB_STATE_SUCCEEDED:
                        logs.info(f"Job {job_name} completed successfully, processing results")

                        # Get inlined responses from the completed job
                        inlined_responses = gemini_job.inlined_responses
                        if not inlined_responses:
                            logs.error(f"Job {job_name} has no inlined responses")
                            await batch_job.mark_failed(conn, "No inlined responses in completed job")
                            continue

                        try:
                            (
                                nb_messages,
                                nb_discussions,
                                nb_input_tokens,
                                nb_output_tokens,
                            ) = await v2.process_gemini_batch_result(conn, batch_job, inlined_responses)
                            await batch_job.mark_completed(conn)
                            logs.info(
                                f"Job {job_name} processed: {nb_messages} messages, "
                                f"{nb_discussions} discussions, "
                                f"{nb_input_tokens} input tokens, {nb_output_tokens} output tokens"
                            )
                        except Exception as e:
                            logs.error(f"Error processing results for job {job_name}: {e}")
                            await batch_job.mark_failed(conn, str(e))
                            if debug:
                                raise

                    elif gemini_job.state in (
                        JobState.JOB_STATE_FAILED,
                        JobState.JOB_STATE_CANCELLED,
                        JobState.JOB_STATE_EXPIRED,
                    ):
                        error_msg = str(gemini_job.error) if gemini_job.error else f"Job state: {gemini_job.state}"
                        logs.warning(f"Job {job_name} failed: {error_msg}")
                        await batch_job.mark_failed(conn, error_msg)

                    else:
                        logs.warning(f"Job {job_name} in unexpected state: {gemini_job.state}")

            except Exception as e:
                logs.error(f"Error in Gemini batch worker loop: {e}")
                if debug:
                    raise

            if not run_forever:
                break

            await asyncio.sleep(sleep_interval)
