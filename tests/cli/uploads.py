import datetime as dt
import json
from typing import Any, cast

import botocore.client
import niquests
from tracktolib.pg_sync import fetch_all, insert_many

from ..conftest import PG_URL
from ..data.db import ChatUpload, gen_chat_upload, gen_telegram_group, gen_user, load_chat_types

TELEGRAM_GROUP = gen_telegram_group()
UNSUPPORTED_SOURCE = "unsupported"


def _chat_type_id(engine, name: str, internal_code: str) -> int:
    with engine.cursor() as cur:
        cur.execute(
            """
            INSERT INTO general.chat_types (name, internal_code)
            VALUES (%s, %s)
            ON CONFLICT (internal_code) DO NOTHING
            """,
            (name, internal_code),
        )
    engine.commit()
    return load_chat_types(engine, as_dict=True)[internal_code]["id"]


def _insert_uploads(engine, *, bad_chat_type_id: int | None = None) -> tuple[ChatUpload, ChatUpload]:
    """Insert one good telegram upload and one bad upload, oldest first."""
    user = gen_user()
    if bad_chat_type_id is None:
        bad_chat_type_id = _chat_type_id(engine, "Unsupported", UNSUPPORTED_SOURCE)
    with engine.cursor() as cur:
        insert_many(cur, "general.users", [user])
    engine.commit()

    chat_types = load_chat_types(engine, as_dict=True)
    good = gen_chat_upload(user["id"], chat_type_id=chat_types["telegram"]["id"])
    bad = gen_chat_upload(user["id"], chat_type_id=bad_chat_type_id)
    # The bad upload is older so it is picked first by the batch query.
    bad["created_at"] = dt.datetime(2024, 1, 1, 10, 0, 0, tzinfo=dt.timezone.utc)
    good["created_at"] = dt.datetime(2024, 1, 1, 10, 0, 1, tzinfo=dt.timezone.utc)
    with engine.cursor() as cur:
        insert_many(cur, "general.chat_uploads", [bad, good])
    engine.commit()
    return good, bad


async def _s3_get_object(**_: Any) -> bytes:
    return json.dumps(TELEGRAM_GROUP).encode("utf-8")


def _run_process_uploads(loop) -> list[int]:
    from polarsen.cli.ingest import process_uploads
    from polarsen.pg import get_conn

    async def _test():
        async with get_conn(PG_URL) as conn:
            async with niquests.AsyncSession() as session:
                # Mirror the listener, which runs the batch inside a transaction.
                async with conn.transaction():
                    return await process_uploads(
                        client=session,
                        conn=conn,
                        s3_client=cast(botocore.client.BaseClient, None),
                    )

    return loop.run_until_complete(_test())


def _load_uploads(engine) -> dict[int, dict]:
    rows = fetch_all(engine, "SELECT id, processed_at, chat_id, error_count, error_at FROM general.chat_uploads")
    return {row["id"]: row for row in rows}


def test_failed_upload_does_not_block_batch(loop, engine, monkeypatch):
    good, bad = _insert_uploads(engine)
    monkeypatch.setattr("polarsen.cli.ingest.env.CHAT_UPLOADS_S3_BUCKET", "test-bucket")
    monkeypatch.setattr("polarsen.cli.ingest.s3_get_object", _s3_get_object)

    chat_ids = _run_process_uploads(loop)
    uploads = _load_uploads(engine)

    assert len(chat_ids) == 1
    assert uploads[good["id"]]["processed_at"] is not None
    assert uploads[good["id"]]["chat_id"] == chat_ids[0]
    assert uploads[bad["id"]]["processed_at"] is None
    assert uploads[bad["id"]]["error_count"] == 1
    assert uploads[bad["id"]]["error_at"] is not None
    assert len(fetch_all(engine, "SELECT id FROM general.chats")) == 1


def test_failed_upload_stops_after_retry_cap(loop, engine, monkeypatch):
    good, bad = _insert_uploads(engine)
    monkeypatch.setattr("polarsen.cli.ingest.env.CHAT_UPLOADS_S3_BUCKET", "test-bucket")
    monkeypatch.setattr("polarsen.cli.ingest.env.MAX_INGESTION_RETRIES", 2)
    monkeypatch.setattr("polarsen.cli.ingest.s3_get_object", _s3_get_object)

    assert len(_run_process_uploads(loop)) == 1
    assert _run_process_uploads(loop) == []
    assert _run_process_uploads(loop) == []

    uploads = _load_uploads(engine)
    assert uploads[bad["id"]]["processed_at"] is None
    assert uploads[bad["id"]]["error_count"] == 2
    assert uploads[good["id"]]["error_count"] == 0
    assert len(fetch_all(engine, "SELECT id FROM general.chats")) == 1


def test_save_failure_does_not_abort_batch(loop, engine, monkeypatch):
    telegram_id = load_chat_types(engine, as_dict=True)["telegram"]["id"]
    good, bad = _insert_uploads(engine, bad_chat_type_id=telegram_id)

    oversized_group = gen_telegram_group()
    oversized_text = "x" * 10_001
    oversized_group["messages"][0]["text"] = oversized_text
    oversized_group["messages"][0]["text_entities"] = [{"type": "plain", "text": oversized_text}]

    async def _s3_get(**kwargs: Any) -> bytes:
        if kwargs["key"] == bad["file_path"]:
            return json.dumps(oversized_group).encode("utf-8")
        return json.dumps(TELEGRAM_GROUP).encode("utf-8")

    monkeypatch.setattr("polarsen.cli.ingest.env.CHAT_UPLOADS_S3_BUCKET", "test-bucket")
    monkeypatch.setattr("polarsen.cli.ingest.s3_get_object", _s3_get)

    chat_ids = _run_process_uploads(loop)
    uploads = _load_uploads(engine)

    assert len(chat_ids) == 1
    assert uploads[good["id"]]["processed_at"] is not None
    assert uploads[bad["id"]]["processed_at"] is None
    assert uploads[bad["id"]]["error_count"] == 1
    # The failed savepoint left no partial chat, user, or message rows behind.
    assert len(fetch_all(engine, "SELECT id FROM general.chats")) == 1
    assert len(fetch_all(engine, "SELECT id FROM general.chat_users")) == 2
    assert len(fetch_all(engine, "SELECT id FROM general.chat_messages")) == 3
