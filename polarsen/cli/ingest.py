import json
import logging

import asyncpg
import botocore.client
import niquests
from rich.progress import track

from polarsen import env
from polarsen.db import TelegramGroup, ChatUpload, DbChat, check_access_to_chat
from polarsen.logs import logs
from polarsen.s3_utils import s3_get_object

PENDING_CHAT_UPLOADS_QUERY = """
WITH next_uploads AS (
  SELECT id, user_id
  FROM general.chat_uploads
  WHERE processed_at IS NULL
  ORDER BY created_at
  FOR UPDATE SKIP LOCKED
  LIMIT $1
)
SELECT
  cu.id,
  cu.file_path,
  c.internal_code AS chat_source,
  cu.user_id as uploaded_by
FROM next_uploads n
JOIN general.chat_uploads cu ON cu.id = n.id
LEFT JOIN general.chat_types c ON c.id = cu.chat_type_id;
"""


async def fetch_pending_uploads(conn: asyncpg.Connection, limit: int = 10_000) -> list[asyncpg.Record]:
    records = await conn.fetch(PENDING_CHAT_UPLOADS_QUERY, limit)
    return records


async def _lock_chat_ingest(conn: asyncpg.Connection, internal_code: str) -> None:
    """Serialize ingestion of one chat across concurrent workers.

    Two workers can both see no chat for the same internal_code, skip the access
    check, then race on the insert. The winner creates the chat and the loser's
    upload merges into it without any check. The transaction-scoped advisory
    lock makes the second worker wait until the first commits, so it sees the
    chat and runs the access check against it.
    """
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", internal_code)


async def process_uploads(
    client: niquests.AsyncSession,
    conn: asyncpg.Connection,
    s3_client: botocore.client.BaseClient,
    *,
    show_progress: bool = False,
    limit: int = 10_000,
    logger: None | logging.LoggerAdapter = None,
) -> list[int]:
    """
    Process pending chat uploads from S3 and ingest them into the database.
    Will mark uploads as processed once done.
    Returns the list of chat IDs that were processed.
    """
    _logs = logger or logs
    bucket = env.CHAT_UPLOADS_S3_BUCKET
    if not bucket:
        raise ValueError("CHAT_UPLOADS_S3_BUCKET must be set")
    pending_uploads = await fetch_pending_uploads(conn, limit=limit)
    if not pending_uploads:
        _logs.debug("No pending chat uploads to process.")
        return []
    _logs.info(f"Found {len(pending_uploads)} pending chat uploads to process.")

    chat_ids = []
    for _upload in track(pending_uploads, disable=not show_progress, show_speed=True, description="Uploads..."):
        upload_id = _upload["id"]
        file_path = _upload["file_path"]
        chat_source = _upload["chat_source"]
        uploaded_by = _upload["uploaded_by"]

        _logs.debug(f"Processing chat upload {upload_id=} {file_path=} {chat_source=}")

        file_data = await s3_get_object(s3=s3_client, client=client, bucket=bucket, key=file_path)
        if file_data is None:
            _logs.error(f"Failed to fetch file {file_path!r} from S3 for chat upload {upload_id}. Skipping.")
            continue

        match chat_source:
            case "telegram":
                group = TelegramGroup.load(json.loads(file_data.decode("utf-8")), show_progress=show_progress)
                async with conn.transaction():
                    internal_code = str(group.group_id)
                    await _lock_chat_ingest(conn, internal_code)
                    existing_chat_id = (await DbChat.get_ids(conn, [internal_code])).get(internal_code)
                    if existing_chat_id is not None and not await check_access_to_chat(
                        conn, existing_chat_id, uploaded_by
                    ):
                        _logs.error(
                            f"User {uploaded_by} does not have access to chat {existing_chat_id}; "
                            f"rejecting upload {upload_id}."
                        )
                        await ChatUpload.mark_processed(conn, chat_id=existing_chat_id, upload_id=upload_id)
                        continue
                    chat_id = await group.save(conn=conn, created_by=uploaded_by)
                    await ChatUpload.mark_processed(conn, chat_id=chat_id, upload_id=upload_id)
            case _:
                raise ValueError(f"Unsupported chat source {chat_source!r}")

        chat_ids.append(chat_id)
    return chat_ids
