import datetime as dt
import unittest.mock
from typing import NamedTuple, Callable, Any

import psycopg
import pytest
from tracktolib.pg_sync import insert_many
from tracktolib.tests import assert_equals

from tests.data.db import (
    gen_user,
    gen_chat,
    gen_chat_user,
    gen_chat_message,
    gen_message_group_method,
    gen_message_group,
    gen_message_group_chat,
    gen_mistral_group_embedding,
    load_chat_types,
    DEFAULT_DATETIME,
)
from polarsen.common import search as search_module
from polarsen.common.search import search_close_messages

EMBEDDING = [0.1] * 1024
USER = gen_user()
METHOD = gen_message_group_method()
CHAT = gen_chat(created_by=USER["id"])


def _insert_base_data(cur: psycopg.Cursor):
    """Insert common base data (user, method)."""
    insert_many(cur, "general.users", [USER])
    insert_many(cur, "ai.message_group_methods", [METHOD])


def _insert_single_group(engine: psycopg.Connection) -> dict:
    """Setup a single message group with 2 messages."""
    chat_types = load_chat_types(engine, as_dict=True)
    chat_user = gen_chat_user(chat_id=CHAT["id"], chat_source_id=chat_types["telegram"]["id"])

    msg1 = gen_chat_message(
        chat_user_id=chat_user["id"],
        chat_id=CHAT["id"],
        message="When is the deadline?",
        sent_at=DEFAULT_DATETIME,
    )
    msg2 = gen_chat_message(
        chat_user_id=chat_user["id"],
        chat_id=CHAT["id"],
        message="Next Friday",
        sent_at=DEFAULT_DATETIME + dt.timedelta(minutes=1),
    )

    group = gen_message_group(
        chat_id=CHAT["id"],
        group_method_id=METHOD["id"],
        summary="Discussion about project deadlines",
        title="Project Planning",
        meta={"day": DEFAULT_DATETIME.strftime("%Y-%m-%d")},
    )

    group_chat1 = gen_message_group_chat(group_id=group["id"], chat_id=CHAT["id"], msg_id=msg1["id"])
    group_chat2 = gen_message_group_chat(group_id=group["id"], chat_id=CHAT["id"], msg_id=msg2["id"])

    with engine.cursor() as cur:
        _insert_base_data(cur)
        insert_many(cur, "general.chats", [CHAT])
        insert_many(cur, "general.chat_users", [chat_user])
        insert_many(cur, "general.chat_messages", [msg1, msg2])
        insert_many(cur, "ai.message_groups", [group])
        insert_many(cur, "ai.message_group_chats", [group_chat1, group_chat2])
        insert_many(cur, "ai.mistral_group_embeddings", [gen_mistral_group_embedding(group["id"], EMBEDDING)])
    engine.commit()

    return {
        "chat_id": CHAT["id"],
        "embedding": EMBEDDING,
        "group": group,
        "chat_user": chat_user,
        "messages": [msg1, msg2],
    }


def _insert_empty_chat(engine: psycopg.Connection) -> dict:
    """Setup a chat with no message groups."""
    with engine.cursor() as cur:
        _insert_base_data(cur)
        insert_many(cur, "general.chats", [CHAT])
    engine.commit()

    return {"chat_id": CHAT["id"], "embedding": EMBEDDING}


def _insert_multiple_groups(engine: psycopg.Connection) -> dict:
    """Setup 5 message groups to test limit."""
    chat_types = load_chat_types(engine, as_dict=True)
    chat_user = gen_chat_user(chat_id=CHAT["id"], chat_source_id=chat_types["telegram"]["id"])

    with engine.cursor() as cur:
        _insert_base_data(cur)

        insert_many(cur, "general.chats", [CHAT])
        insert_many(cur, "general.chat_users", [chat_user])

        for i in range(5):
            msg = gen_chat_message(
                chat_user_id=chat_user["id"],
                chat_id=CHAT["id"],
                message=f"Message {i}",
            )
            insert_many(cur, "general.chat_messages", [msg])

            group = gen_message_group(
                chat_id=CHAT["id"],
                group_method_id=METHOD["id"],
                summary=f"Summary {i}",
                title=f"Title {i}",
                meta={"day": f"2024-01-{15 + i:02d}"},
            )
            insert_many(cur, "ai.message_groups", [group])
            insert_many(cur, "ai.message_group_chats", [gen_message_group_chat(group["id"], CHAT["id"], msg["id"])])
            insert_many(cur, "ai.mistral_group_embeddings", [gen_mistral_group_embedding(group["id"], EMBEDDING)])
    engine.commit()

    return {"chat_id": CHAT["id"], "embedding": EMBEDDING}


def _insert_two_chats(engine: psycopg.Connection) -> dict:
    """Setup two chats with one group each to test filtering."""
    chat_types = load_chat_types(engine, as_dict=True)
    telegram_type_id = chat_types["telegram"]["id"]

    chat_user1 = gen_chat_user(chat_id=CHAT["id"], chat_source_id=telegram_type_id)
    chat2 = gen_chat(created_by=USER["id"])
    chat_user2 = gen_chat_user(chat_id=chat2["id"], chat_source_id=telegram_type_id)

    with engine.cursor() as cur:
        _insert_base_data(cur)

        msg1 = gen_chat_message(chat_user_id=chat_user1["id"], chat_id=CHAT["id"], message="Chat 1 message")
        group1 = gen_message_group(
            chat_id=CHAT["id"], group_method_id=METHOD["id"], summary="Chat 1 summary", meta={"day": "2024-01-15"}
        )

        msg2 = gen_chat_message(chat_user_id=chat_user2["id"], chat_id=chat2["id"], message="Chat 2 message")
        group2 = gen_message_group(
            chat_id=chat2["id"], group_method_id=METHOD["id"], summary="Chat 2 summary", meta={"day": "2024-01-16"}
        )

        insert_many(cur, "general.chats", [CHAT, chat2])
        insert_many(cur, "general.chat_users", [chat_user1, chat_user2])

        insert_many(cur, "general.chat_messages", [msg1])
        insert_many(cur, "ai.message_groups", [group1])
        insert_many(cur, "ai.message_group_chats", [gen_message_group_chat(group1["id"], CHAT["id"], msg1["id"])])
        insert_many(cur, "ai.mistral_group_embeddings", [gen_mistral_group_embedding(group1["id"], EMBEDDING)])

        insert_many(cur, "general.chat_messages", [msg2])
        insert_many(cur, "ai.message_groups", [group2])
        insert_many(cur, "ai.message_group_chats", [gen_message_group_chat(group2["id"], chat2["id"], msg2["id"])])
        insert_many(cur, "ai.mistral_group_embeddings", [gen_mistral_group_embedding(group2["id"], EMBEDDING)])
    engine.commit()

    return {"chat_id": CHAT["id"], "embedding": EMBEDDING}


def _check_single_group(result: list, ctx: dict):
    """Assert single group with correct messages."""
    group = ctx["group"]
    chat_user = ctx["chat_user"]
    messages = ctx["messages"]

    assert len(result) == 1
    assert result[0]["group_id"] == group["id"]
    assert result[0]["summary"] == "Discussion about project deadlines"
    assert result[0]["title"] == "Project Planning"
    assert result[0]["day"] == DEFAULT_DATETIME.date()

    expected_messages = [
        {
            "id": messages[0]["id"],
            "user": chat_user["username"],
            "message": "When is the deadline?",
            "sent_at": DEFAULT_DATETIME.strftime("%Y-%m-%d %H:%M:%S"),
            "reply_to_id": None,
        },
        {
            "id": messages[1]["id"],
            "user": chat_user["username"],
            "message": "Next Friday",
            "sent_at": (DEFAULT_DATETIME + dt.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
            "reply_to_id": None,
        },
    ]
    assert_equals(result[0]["messages"], expected_messages, ignore_order=True)


def _check_empty(result: list, _ctx: dict):
    assert_equals(result, [])


def _check_limited(result: list, _ctx: dict):
    assert len(result) == 2


def _check_chat1_only(result: list, _ctx: dict):
    assert len(result) == 1
    assert result[0]["summary"] == "Chat 1 summary"


class SearchParams(NamedTuple):
    setup_fn: Callable[[psycopg.Connection], dict[str, Any]] | None
    check_fn: Callable[[list, dict[str, Any]], None]
    query: str = "test"
    limit: int = 3
    embedding: list[float] | None = None


_single_group = pytest.param(
    SearchParams(
        setup_fn=_insert_single_group,
        check_fn=_check_single_group,
        query="What was discussed about deadlines?",
        limit=3,
    ),
    id="returns_close_embeddings",
)

_empty_chat = pytest.param(
    SearchParams(
        setup_fn=_insert_empty_chat,
        check_fn=_check_empty,
        query="something that doesn't exist",
        limit=3,
    ),
    id="empty_when_no_matches",
)

_multiple_groups = pytest.param(
    SearchParams(
        setup_fn=_insert_multiple_groups,
        check_fn=_check_limited,
        query="test",
        limit=2,
    ),
    id="respects_limit",
)

_two_chats = pytest.param(
    SearchParams(
        setup_fn=_insert_two_chats,
        check_fn=_check_chat1_only,
        query="test",
        limit=3,
    ),
    id="filters_by_chat",
)


@pytest.mark.parametrize("test_param", [_single_group, _empty_chat, _multiple_groups, _two_chats])
def test_search_close_messages(engine, aengine, mock_session, loop, monkeypatch, test_param: SearchParams):
    """Parameterized test for search_close_messages."""
    if test_param.setup_fn:
        ctx = test_param.setup_fn(engine)
        embedding = ctx["embedding"]
    else:
        ctx = {}
        embedding = test_param.embedding or EMBEDDING

    # fetch_embeddings returns (list[list[float]], UsageToken) - a list of embeddings
    mock_embeddings_fn = unittest.mock.AsyncMock(return_value=([embedding], {"total": 10, "input": 10, "output": 0}))
    monkeypatch.setattr(search_module, "_EMBEDDINGS_FNS", {"mistral": mock_embeddings_fn})

    result = loop.run_until_complete(
        search_close_messages(
            session=mock_session,
            conn=aengine,
            chat_id=ctx.get("chat_id", 0),
            question=test_param.query,
            model_name="mistral",
            limit=test_param.limit,
        )
    )

    test_param.check_fn(result, ctx)
