import json
import logging

import asyncpg
import botocore.client
import niquests
from rich.progress import track

from polarsen import env
from polarsen.db import TelegramGroup, ChatUpload
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
                chat_id = await group.save(conn=conn, created_by=uploaded_by)
            case _:
                raise ValueError(f"Unsupported chat source {chat_source!r}")

        await ChatUpload.mark_processed(conn, chat_id=chat_id, upload_id=upload_id)
        chat_ids.append(chat_id)
    return chat_ids
