import datetime as dt
from typing import Literal

import asyncpg
from niquests import AsyncSession
from rich.progress import track

from polarsen.common.models import mistral
from polarsen.common.models.utils import TooManyRequestsError
from polarsen.db import Requests, MistralGroupEmbeddings
from polarsen.logs import logs
from .conversations.utils import retry_async

__all__ = ("gen_embeddings",)


async def get_groups(
    conn: asyncpg.Connection,
    chat_id: int,
    force: bool = False,
    days: list[dt.date] | None = None,
    from_date: dt.date | None = None,
):
    query = """
            SELECT g.id, g.summary, g.title, msg.messages, (g.meta ->> 'day')::date as day
            FROM ai.message_groups g
                     left join LATERAL (
                select array_agg(cm.message order by cm.sent_at) as messages
                from ai.message_group_chats m
                         left join general.chat_messages cm on cm.id = m.msg_id
                where m.group_id = g.id
                  and cm.message <> ''
                ) msg on true
                     left join ai.mistral_group_embeddings e on e.group_id = g.id
            WHERE ($1 IS TRUE OR e.embedding IS NULL)
              AND ($2::date[] IS NULL OR (g.meta ->> 'day')::date = any ($2))
              AND ($3::date IS NULL OR (g.meta ->> 'day')::date >= $3)
              AND g.chat_id = $4
            ORDER BY g.meta ->> 'day'
            """
    data = await conn.fetch(query, force, days, from_date, chat_id)
    return data


def retry_embeddings_generation(
    max_attempts: int = 3,
    delay: float = 2.0,
    backoff_factor: float = 1.5,
):
    """
    Specialized retry decorator for conversation segmentation function.
    Retries on network errors, API rate limits, and temporary service issues.
    """
    # Common exceptions that should trigger retries for API calls
    retryable_exceptions = TooManyRequestsError

    def log_retry_attempt(exception: Exception, attempt: int, delay: float):
        logs.warning(
            f"Conversation embeddings retry {attempt}: {type(exception).__name__}: {exception}. "
            f"Retrying in {delay:.1f}s"
        )

    return retry_async(
        max_attempts=max_attempts,
        delay=delay,
        backoff_factor=backoff_factor,
        jitter=True,
        exceptions=retryable_exceptions,
        on_retry=log_retry_attempt,
        reraise_on_final_attempt=True,
    )


@retry_embeddings_generation(max_attempts=5, delay=3, backoff_factor=2)
async def gen_group_embeddings(
    conn: asyncpg.Connection,
    session: AsyncSession,
    group: dict,
    source: Literal["mistral"] = "mistral",
):
    inputs = [group["title"], group["summary"]] + group["messages"]
    embedding, tokens = await mistral.fetch_embeddings(session, inputs=inputs)
    await Requests.load("embedding", tokens).save(conn)
    if source == "mistral":
        await MistralGroupEmbeddings(
            group_id=group["id"],
            embedding=embedding,
        ).save(conn)
    else:
        raise ValueError(f"Source {source} is not supported for embeddings generation")


async def gen_embeddings(
    conn: asyncpg.Connection,
    session: AsyncSession,
    chat_id: int,
    force: bool = False,
    source: Literal["mistral"] = "mistral",
    show_progress: bool = False,
    days: list[dt.date] | None = None,
    from_date: dt.date | None = None,
):
    groups = await get_groups(conn, chat_id=chat_id, force=force, days=days, from_date=from_date)
    nb_groups = len(groups)
    logs.info(f"Found {nb_groups} groups to process")

    for i, group in enumerate(track(groups, show_speed=True, disable=not show_progress)):
        logs.debug(f"Processing group {group['id']} ({group['day']}): {group['title']} ({i}/{nb_groups})")
        await gen_group_embeddings(conn, session, group, source=source)
