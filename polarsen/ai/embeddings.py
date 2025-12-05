import datetime as dt
import itertools
from typing import NotRequired, TypedDict
import asyncpg
from niquests import AsyncSession
from pydantic import TypeAdapter
from rich.progress import track

from polarsen.common.models import mistral  # Could be lazy
from polarsen.common.utils import setup_session_model
from polarsen.db import Requests, MistralGroupEmbeddings
from polarsen.logs import logs

__all__ = (
    "gen_embeddings",
    "DEFAULT_EMBEDDING_MODEL",
    "gen_group_embeddings",
    "EmbeddingGroup",
    "EmbeddingGroupAdapter",
    "gen_groups_embeddings",
)

DEFAULT_EMBEDDING_MODEL = "mistral-embed"


class EmbeddingGroup(TypedDict):
    id: int
    title: str
    summary: str
    messages: list[str]
    user_id: int
    day: NotRequired[dt.date]


EmbeddingGroupAdapter = TypeAdapter(EmbeddingGroup)


async def get_groups(
    conn: asyncpg.Connection,
    chat_id: int,
    force: bool = False,
    days: list[dt.date] | None = None,
    from_date: dt.date | None = None,
) -> list[EmbeddingGroup]:
    query = """
            SELECT g.id,
                   g.summary,
                   g.title,
                   msg.messages,
                   (g.meta ->> 'day')::DATE AS day,
                   c.created_by             AS user_id
            FROM ai.message_groups g
                     LEFT JOIN general.chats c ON c.id = g.chat_id
                     LEFT JOIN LATERAL (
                SELECT ARRAY_AGG(cm.message ORDER BY cm.sent_at) AS messages
                FROM ai.message_group_chats m
                         LEFT JOIN general.chat_messages cm ON cm.id = m.msg_id
                WHERE m.group_id = g.id
                  AND cm.message <> ''
                ) msg ON TRUE
                     LEFT JOIN ai.mistral_group_embeddings e ON e.group_id = g.id
            WHERE ($1 IS TRUE OR e.embedding IS NULL)
              AND ($2::DATE[] IS NULL OR (g.meta ->> 'day')::DATE = ANY ($2))
              AND ($3::DATE IS NULL OR (g.meta ->> 'day')::DATE >= $3)
              AND g.chat_id = $4
            ORDER BY g.meta ->> 'day'
            """
    data = await conn.fetch(
        query,
        force,
        days,
        from_date,
        chat_id,
    )
    return [EmbeddingGroupAdapter.validate_python(row) for row in data]


def _get_embed_input_from_group(group: EmbeddingGroup) -> str:
    return " ".join([group["title"], group["summary"]] + group["messages"])


async def gen_groups_embeddings(
    conn: asyncpg.Connection,
    session: AsyncSession,
    groups: list[EmbeddingGroup],
    model_name: str = DEFAULT_EMBEDDING_MODEL,
):
    """Bulk generate embeddings for a given group and save them to the database."""
    source, _, _ = setup_session_model(session=session, model_name=model_name)

    # We want to group calls by user to make sure the tracking of tokens is correct
    for user_id, user_groups in itertools.groupby(groups, key=lambda g: g["user_id"]):
        all_inputs = [_get_embed_input_from_group(group) for group in user_groups]
        if source == "mistral":
            embeddings, tokens = await mistral.fetch_embeddings(session, inputs=all_inputs)
            if len(groups) != len(embeddings):
                raise ValueError(
                    f"Number of embeddings {len(embeddings)} does not match number of groups {len(all_inputs)}"
                )
            embeddings = [
                MistralGroupEmbeddings(group_id=group["id"], embedding=embedding)
                for group, embedding in zip(groups, embeddings)
            ]
            await MistralGroupEmbeddings.bulk_save(conn, embeddings=embeddings)
        else:
            raise ValueError(
                f"Source {source!r} (model: {model_name!r}) is not yet supported for embeddings generation"
            )
        await Requests.load("embedding", tokens, user_id=user_id).save(conn)


async def gen_group_embeddings(
    conn: asyncpg.Connection,
    session: AsyncSession,
    user_id: int,
    group: EmbeddingGroup,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    api_key: str | None = None,
):
    """Generate embeddings for a given group and save them to the database."""
    inputs = [group["title"], group["summary"]] + group["messages"]
    source, _, _ = setup_session_model(session=session, model_name=model_name, api_key=api_key)
    if source == "mistral":
        embeddings, tokens = await mistral.fetch_embeddings(session, inputs=inputs)
        await MistralGroupEmbeddings(
            group_id=group["id"],
            embedding=embeddings[0],
        ).save(conn)
    else:
        raise ValueError(f"Source {source} is not supported for embeddings generation")

    await Requests.load("embedding", tokens, user_id=user_id).save(conn)


async def gen_embeddings(
    conn: asyncpg.Connection,
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    force: bool = False,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    show_progress: bool = False,
    days: list[dt.date] | None = None,
    from_date: dt.date | None = None,
):
    groups = await get_groups(conn, chat_id=chat_id, force=force, days=days, from_date=from_date)
    nb_groups = len(groups)
    logs.info(f"Found {nb_groups} groups to process")

    for i, group in enumerate(track(groups, show_speed=True, disable=not show_progress)):
        logs.debug(f"Processing group {group['id']} ({group.get('day')}): {group['title']} ({i}/{nb_groups})")
        await gen_group_embeddings(conn, session, model_name=model_name, group=group, user_id=user_id)
