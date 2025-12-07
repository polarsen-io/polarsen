import datetime as dt
import unittest.mock
from contextlib import nullcontext
from typing import NamedTuple, Callable

import psycopg
import pytest
from tracktolib.pg_sync import insert_many, fetch_all

from ..conftest import PG_URL
from ..data.db import (
    gen_user,
    gen_chat,
    gen_chat_user,
    gen_chat_message,
    gen_message_group_method,
    gen_message_group,
    gen_message_group_chat,
    load_chat_types,
    DEFAULT_DATETIME,
)


def _insert_message_groups(engine: psycopg.Connection, group_meta: dict | None = None):
    users_data = [gen_user(), gen_user(api_keys={"mistral": "test-key-1234"})]
    chats_data = [
        gen_chat(created_by=users_data[0]["id"]),
        gen_chat(created_by=users_data[1]["id"]),
    ]

    with engine.cursor() as cur:
        insert_many(cur, "general.users", users_data)
        insert_many(cur, "general.chats", chats_data)
    engine.commit()

    # Load existing chat types
    chat_types = load_chat_types(engine, as_dict=True)
    telegram_chat_type = chat_types["telegram"]

    # Create chat users
    chat_users_data = [
        gen_chat_user(chat_id=chats_data[0]["id"], chat_source_id=telegram_chat_type["id"]),
        gen_chat_user(chat_id=chats_data[1]["id"], chat_source_id=telegram_chat_type["id"]),
    ]

    # Create a message group method
    method_data = [gen_message_group_method(name="Test Method", internal_code="test_method")]

    # Create message groups
    message_groups_data = [
        gen_message_group(
            chat_id=chats_data[0]["id"],
            group_method_id=method_data[0]["id"],
            summary="Test summary 1",
            title="Test title 1",
            meta=group_meta,
        ),
        gen_message_group(
            chat_id=chats_data[1]["id"],
            group_method_id=method_data[0]["id"],
            summary="Test summary 2",
            title="Test title 2",
            meta=group_meta,
        ),
    ]

    # Create chat messages
    chat_messages_data = [
        gen_chat_message(
            chat_user_id=chat_users_data[0]["id"],
            chat_id=chats_data[0]["id"],
            message="Test message 1 for group 1",
            sent_at=DEFAULT_DATETIME,
        ),
        gen_chat_message(
            chat_user_id=chat_users_data[0]["id"],
            chat_id=chats_data[0]["id"],
            message="Test message 2 for group 1",
            sent_at=DEFAULT_DATETIME + dt.timedelta(minutes=1),
        ),
        gen_chat_message(
            chat_user_id=chat_users_data[1]["id"],
            chat_id=chats_data[1]["id"],
            message="Test message 1 for group 2",
            sent_at=DEFAULT_DATETIME + dt.timedelta(minutes=2),
        ),
    ]

    # Link message groups to chat messages
    message_group_chats_data = [
        gen_message_group_chat(
            group_id=message_groups_data[0]["id"], chat_id=chats_data[0]["id"], msg_id=chat_messages_data[0]["id"]
        ),
        gen_message_group_chat(
            group_id=message_groups_data[0]["id"], chat_id=chats_data[0]["id"], msg_id=chat_messages_data[1]["id"]
        ),
        gen_message_group_chat(
            group_id=message_groups_data[1]["id"], chat_id=chats_data[1]["id"], msg_id=chat_messages_data[2]["id"]
        ),
    ]

    with engine.cursor() as cur:
        insert_many(cur, "general.chat_users", chat_users_data)
        insert_many(cur, "ai.message_group_methods", method_data)
        insert_many(cur, "ai.message_groups", message_groups_data)
        insert_many(cur, "general.chat_messages", chat_messages_data)
        insert_many(cur, "ai.message_group_chats", message_group_chats_data)
    engine.commit()


class EmbeddingsParams(NamedTuple):
    setup_fn: Callable[[psycopg.Connection], None] | None
    mock_fn: unittest.mock.AsyncMock
    check_fn: Callable[[psycopg.Connection, unittest.mock.AsyncMock], None]


def _check_no_data(_: psycopg.Connection, mock: unittest.mock.AsyncMock):
    mock.assert_not_awaited()


_no_data = pytest.param(
    EmbeddingsParams(setup_fn=None, mock_fn=unittest.mock.AsyncMock(), check_fn=_check_no_data), id="no-data"
)


def _assert_meta_done(meta: dict | None):
    assert meta, "Message group meta should not be empty"
    assert meta["embeddings_status"] == "done", "Message group embeddings status should be 'done'"
    assert meta["embeddings_done_at"] is not None, "Message group should have embeddings_done_at timestamp"
    assert meta["embeddings_started_at"] is not None, "Message group should have embeddings_started_at timestamp"


def _check_with_data(engine: psycopg.Connection, mock: unittest.mock.AsyncMock):
    mock.assert_awaited()
    groups = fetch_all(engine, "select id, meta from ai.message_groups order by id")
    for x in groups:
        _assert_meta_done(x["meta"])


_with_data = pytest.param(
    EmbeddingsParams(
        setup_fn=lambda engine: _insert_message_groups(engine),
        mock_fn=unittest.mock.AsyncMock(),
        check_fn=_check_with_data,
    ),
    id="with-data",
)


_processing_is_ignored = pytest.param(
    EmbeddingsParams(
        setup_fn=lambda engine: _insert_message_groups(engine, group_meta={"embeddings_status": "processing"}),
        mock_fn=unittest.mock.AsyncMock(),
        check_fn=_check_no_data,
    ),
    id="processing is ignored",
)


def _check_with_errors(engine: psycopg.Connection, mock: unittest.mock.AsyncMock):
    mock.assert_awaited()
    groups = fetch_all(engine, "select id, meta from ai.message_groups order by id")
    for group in groups:
        assert group["meta"]["embeddings_status"] == "error"
        assert group["meta"]["embeddings_error_at"] is not None
        assert group["meta"]["embeddings_started_at"] is not None
        assert group["meta"]["embeddings_error_message"] is not None


_processing_error = pytest.param(
    EmbeddingsParams(
        setup_fn=lambda engine: _insert_message_groups(engine),
        mock_fn=unittest.mock.AsyncMock(side_effect=Exception("Test error")),
        check_fn=_check_with_errors,
    ),
    id="processing error handled",
)


@pytest.mark.parametrize("test_param", [_no_data, _with_data, _processing_is_ignored, _processing_error])
def test_embeddings_listener(loop, engine, monkeypatch, test_param):
    if test_param.setup_fn is not None:
        test_param.setup_fn(engine)

    monkeypatch.setattr("polarsen.cli.listener.gen_groups_embeddings", test_param.mock_fn)
    from polarsen.cli.listener import process_embeddings_worker
    from polarsen.pg import get_pool

    async def _test():
        async with get_pool(PG_URL) as pool:
            await process_embeddings_worker(
                pool=pool,
                worker_id=0,
                sleep_no_data=0,
                run_forever=False,
                embedding_model="mistral-embed",
            )

    ctx = nullcontext() if test_param.mock_fn.side_effect is None else pytest.raises(Exception)
    with ctx:
        loop.run_until_complete(_test())

    test_param.check_fn(engine, test_param.mock_fn)
