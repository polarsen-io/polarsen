import unittest.mock
from contextlib import nullcontext
from typing import NamedTuple, Callable

import psycopg
import pytest
from tracktolib.pg_sync import insert_many, fetch_all

from ..conftest import PG_URL
from ..data.db import gen_user, gen_chat


def _insert_chat(engine: psycopg.Connection, chat_meta: dict | None = None):
    users_data = [gen_user(), gen_user(api_keys={"gemini": "1234"})]
    chats_data = [
        gen_chat(created_by=users_data[0]["id"], meta=chat_meta),
        gen_chat(created_by=users_data[1]["id"], meta=chat_meta),
    ]

    with engine.cursor() as cur:
        insert_many(cur, "general.users", users_data)
        insert_many(cur, "general.chats", chats_data)
    engine.commit()


class SegmentationParams(NamedTuple):
    setup_fn: Callable[[psycopg.Connection], None] | None
    mock_fn: unittest.mock.AsyncMock
    check_fn: Callable[[psycopg.Connection, unittest.mock.AsyncMock], None]
    process_params: dict | None = None


def _check_no_data(_: psycopg.Connection, mock: unittest.mock.AsyncMock):
    mock.assert_not_awaited()


_no_data = pytest.param(
    SegmentationParams(setup_fn=None, mock_fn=unittest.mock.AsyncMock(), check_fn=_check_no_data), id="no-data"
)


def _assert_meta_done(meta: dict | None):
    assert meta, "Chat meta should not be empty"
    assert meta["status"] == "done", "Chat status should be 'done'"
    assert meta["processing_done_at"] is not None, "Chat should have processing_done_at timestamp"
    assert meta["processing_started_at"] is not None, "Chat should have processing_started_at timestamp"


def _check_with_data_api_keys(engine: psycopg.Connection, mock: unittest.mock.AsyncMock):
    mock.assert_awaited_once()
    chats = fetch_all(engine, "select id, meta from general.chats order by id")
    assert chats[0]["meta"] is None, "Chat without API keys should remain unchanged"
    _assert_meta_done(chats[1]["meta"])


_with_data_api_keys = pytest.param(
    SegmentationParams(
        setup_fn=lambda engine: _insert_chat(engine),
        mock_fn=unittest.mock.AsyncMock(),
        check_fn=_check_with_data_api_keys,
        process_params={"only_with_keys": True},
    ),
    id="with-data and api-keys",
)


def _check_with_data_no_api_keys(engine: psycopg.Connection, mock: unittest.mock.AsyncMock):
    mock.assert_awaited()
    chats = fetch_all(engine, "select id, meta from general.chats order by id")
    for x in chats:
        _assert_meta_done(x["meta"])


_with_data_no_api_keys = pytest.param(
    SegmentationParams(
        setup_fn=lambda engine: _insert_chat(engine),
        mock_fn=unittest.mock.AsyncMock(),
        check_fn=_check_with_data_no_api_keys,
        process_params={"only_with_keys": False},
    ),
    id="with-data and no api-keys",
)

_processing_is_ignored = pytest.param(
    SegmentationParams(
        setup_fn=lambda engine: _insert_chat(engine, chat_meta={"status": "processing"}),
        mock_fn=unittest.mock.AsyncMock(),
        check_fn=_check_no_data,
        process_params={"only_with_keys": False},
    ),
    id="processing is ignored",
)


def _check_with_errors(engine: psycopg.Connection, mock: unittest.mock.AsyncMock):
    mock.assert_awaited()
    chats = fetch_all(engine, "select id, meta from general.chats order by id")
    assert chats[0]["meta"]["status"] == "error"
    assert chats[0]["meta"]["processing_error_at"] is not None
    assert chats[0]["meta"]["processing_started_at"] is not None


_processing_error = pytest.param(
    SegmentationParams(
        setup_fn=lambda engine: _insert_chat(engine),
        mock_fn=unittest.mock.AsyncMock(side_effect=Exception),
        check_fn=_check_with_errors,
        process_params={"only_with_keys": False},
    ),
    id="processing error handled",
)


@pytest.mark.parametrize(
    "test_param", [_no_data, _with_data_api_keys, _with_data_no_api_keys, _processing_is_ignored, _processing_error]
)
def test_segmentation_listener(loop, engine, monkeypatch, test_param):
    if test_param.setup_fn is not None:
        test_param.setup_fn(engine)

    monkeypatch.setattr("polarsen.cli.listener.v2.run_group_messages", test_param.mock_fn)
    from polarsen.cli.listener import process_chat_groups_worker
    from polarsen.pg import get_pool

    async def _test():
        async with get_pool(PG_URL) as pool:
            await process_chat_groups_worker(
                pool=pool,
                worker_id=0,
                params={"model_name": "gemini"},
                sleep_no_data=0,
                run_forever=False,
                **(test_param.process_params or {}),
            )

    ctx = nullcontext() if test_param.mock_fn.side_effect is None else pytest.raises(Exception)
    with ctx:
        loop.run_until_complete(_test())

    test_param.check_fn(engine, test_param.mock_fn)
