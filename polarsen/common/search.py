import datetime as dt
from typing import TypedDict

import asyncpg
import pydantic
from niquests import AsyncSession

from .models import mistral

_EMBEDDINGS_FNS = {"mistral": mistral.fetch_embeddings}

_SEARCH_EMBEDDINGS_QUERY = """
                           select group_id,
                                  g.summary,
                                  g.title,
                                  (embedding <=> $1)       as distance,
                                  (g.meta ->> 'day')::date as day,
                                  _messages.messages
                           from ai.mistral_group_embeddings
                                    left join ai.message_group g on g.id = group_id
                                    left join lateral (
                               select jsonb_agg(
                                              jsonb_build_object(
                                                      'id', cm.id,
                                                      'user', cu.username,
                                                      'message', cm.message,
                                                      'sent_at', (cm.sent_at at time zone 'UTC')::text,
                                                      'reply_to_id', cm.reply_to_id
                                              )
                                      ) as messages
                               from ai.message_group_chats mgc
                                        left join general.chat_messages cm on cm.id = mgc.msg_id
                                        left join general.chat_users cu on cu.id = cm.chat_user_id
                               where mgc.group_id = g.id

                               ) _messages on true
                           where g.chat_id = $3
                           ORDER BY 1 - (embedding <=> $1) desc
                           limit $2
                           """


async def _get_group_messages(
    conn: asyncpg.Connection,
    group_id: int,
):
    messages = await conn.fetch(
        """
        SELECT cm.chat_id,
               cm.id,
               cu.username,
               sent_at,
               language,
               message
        FROM general.chat_messages cm
                 left join general.chat_users cu on cu.id = cm.chat_user_id
        where cm.message <> ''
          and cm.id in (SELECT msg_id from ai.message_group_chats where group_id = $1)
        order by sent_at
        """,
        group_id,
    )

    return messages


class ChatMessage(TypedDict):
    id: int
    user: str
    message: str
    sent_at: str
    reply_to_id: int | None


class CloseEmbedding(TypedDict):
    group_id: int
    summary: str
    title: str
    distance: float
    day: dt.date
    messages: list[ChatMessage]


_CloseEmbeddingType = pydantic.TypeAdapter(CloseEmbedding)


async def search_close_messages(
    session: AsyncSession,
    conn: asyncpg.Connection,
    chat_id: int,
    question: str,
    model_name: str = "mistral",
    limit: int = 3,
) -> list[CloseEmbedding]:
    embeddings_fn = _EMBEDDINGS_FNS[model_name]
    embedding, nb_tokens = await embeddings_fn(session, question)
    results = await conn.fetch(_SEARCH_EMBEDDINGS_QUERY, embedding, limit, chat_id)
    return [_CloseEmbeddingType.validate_python(dict(x)) for x in results]
