import json
from pathlib import Path

import niquests
from piou import Option, Derived, CommandGroup

from polarsen.db.chat import CHAT_SOURCE_MAPPING, TelegramGroup
from polarsen.pg import get_conn
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
