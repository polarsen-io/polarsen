import asyncpg
import botocore.client
import niquests
from rich.progress import track

from polarsen.db import TelegramGroup, ChatUpload
from polarsen import env
from polarsen.logs import logs
from polarsen.s3_utils import s3_get_object
import json

PENDING_CHAT_UPLOADS_QUERY = """
                             SELECT cu.id, cu.file_path, c.internal_code AS chat_source
                             FROM general.chat_uploads cu
                                      LEFT JOIN general.chat_types c ON c.id = cu.chat_type_id
                             where cu.processed_at IS NULL
                             ORDER BY cu.created_at
                             LIMIT $1
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
):
    bucket = env.CHAT_UPLOADS_S3_BUCKET
    if not bucket:
        raise ValueError("CHAT_UPLOADS_S3_BUCKET must be set")
    pending_uploads = await fetch_pending_uploads(conn)
    if not pending_uploads:
        logs.info("No pending chat uploads to process.")
        return
    logs.info(f"Found {len(pending_uploads)} pending chat uploads to process.")

    for _upload in track(pending_uploads, disable=not show_progress, show_speed=True, description="Uploads..."):
        upload_id = _upload["id"]
        file_path = _upload["file_path"]
        chat_source = _upload["chat_source"]
        lang = "fr"

        logs.debug(f"Processing chat upload {upload_id=} {file_path=} {chat_source=}")

        file_data = await s3_get_object(s3=s3_client, client=client, bucket=bucket, key=file_path)
        if file_data is None:
            logs.error(f"Failed to fetch file {file_path!r} from S3 for chat upload {upload_id}. Skipping.")
            continue

        match chat_source:
            case "telegram":
                group = TelegramGroup.load(json.loads(file_data.decode("utf-8")), show_progress=show_progress)
                chat_id = await group.save(conn=conn, lang=lang)
            case _:
                raise ValueError(f"Unsupported chat source {chat_source!r}")

        await ChatUpload.mark_processed(conn, chat_id=chat_id, upload_id=upload_id)
