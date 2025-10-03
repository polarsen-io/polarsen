import datetime as dt

from niquests import AsyncSession
from piou import CommandGroup, Option, Derived

from polarsen.ai.conversations import v2
from polarsen.ai.embeddings import gen_embeddings
from polarsen.common.models import mistral
from polarsen.db import DbChat
from polarsen.logs import logs
from polarsen.pg import get_conn
from polarsen.utils import get_pg_url

ai_group = CommandGroup("ai", help="AI commands")


@ai_group.command("gen-embeddings", help="Generate embeddings for messages in discussions")
async def run_gen_embeddings(
    pg_url=Derived(get_pg_url),
    chat_internal_code: str = Option(..., "--chat", help="Chat ID to process"),
    force: bool = Option(False, "--force", help="Force reprocessing"),
    show_progress: bool = Option(False, "--progress", help="Show progress bar"),
    from_date: dt.date | None = Option(None, "--from-date", help="Starting date for processing"),
    days: list[dt.date] | None = Option(None, "--days", help=" Days to run"),
):
    async with get_conn(pg_url) as conn:
        chat_id = (await DbChat.get_ids(conn, [chat_internal_code]))[chat_internal_code]
        async with AsyncSession() as session:
            mistral.set_headers(session)
            await gen_embeddings(
                conn, session, chat_id=chat_id, days=days, from_date=from_date, force=force, show_progress=show_progress
            )
            logs.info("Embeddings generation completed")


@ai_group.command("gen-discussions", help="Generate discussions from messages, summaries and embeddings")
async def run_gen_discussions(
    pg_url=Derived(get_pg_url),
    force: bool = Option(False, "--force", help="Force reprocessing"),
    show_progress: bool = Option(False, "--progress", help="Show progress bar"),
    lang: str = Option("french", "--lang", help="Lang to use for summaries / titles"),
    chat_internal_code: str = Option(..., "--chat", help="Chat ID to process"),
    # Only v2
    model_name: str = Option(v2.SEGMENTATION_MODEL, "--model", help="Model to use for the segmentation"),
    from_date: dt.date | None = Option(None, "--from-date", help="[V2] Start date for processing"),
    days: list[dt.date] | None = Option(None, "--days", help="[V2] Days to run"),
    temperature: float | None = Option(None, "--temperature", help="[V2] Model temperature"),
    agent_name: str | None = Option(None, "--agent", help="Agent to use for the segmentation"),
    enable_thinking: bool = Option(False, "--thinking", help="Enable thinking mode"),
    timeout: int = Option(5 * 60, "--timeout", help="Timeout for http requests"),
):
    async with get_conn(pg_url) as conn:
        params: v2.ParamsV2 = {
            "model_name": model_name,
            "agent_name": agent_name,
            "disable_thinking": not enable_thinking,
        }
        if days:
            params["days"] = days
        if from_date is not None:
            params["from_date"] = from_date
        if temperature is not None:
            params["temperature"] = temperature
        chat_id = (await DbChat.get_ids(conn, [chat_internal_code]))[chat_internal_code]
        async with AsyncSession(timeout=timeout) as session:
            await v2.run_group_messages(
                conn, session=session, show_progress=show_progress, force=force, lang=lang, chat_id=chat_id, **params
            )
