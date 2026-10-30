import asyncio
import datetime as dt
import io
import json
import os

import asyncpg
from minio import Minio
from tracktolib.pg_sync import fetch_all, fetch_one, insert_many

from polarsen import env
from tests.data.db import (
    gen_chat,
    gen_chat_upload,
    gen_chat_user,
    gen_telegram_group,
    gen_user,
    load_chat_types,
)
from tests.data.utils import Fake


def test_process_uploads_rejects_inaccessible_existing_chat(engine, minio_client: Minio, loop, aengine):
    bucket = os.getenv("CHAT_UPLOADS_S3_BUCKET", "test-bucket")
    os.environ["CHAT_UPLOADS_S3_BUCKET"] = bucket
    env.CHAT_UPLOADS_S3_BUCKET = bucket
    if not minio_client.bucket_exists(bucket):
        minio_client.make_bucket(bucket)

    chat_types = load_chat_types(engine, as_dict=True)
    owner = gen_user(telegram_id=f"owner-{Fake.id()}")
    other = gen_user(telegram_id=f"other-{Fake.id()}")
    group = gen_telegram_group()
    chat = gen_chat(created_by=owner["id"])
    chat["internal_code"] = str(group["id"])
    chat_user = gen_chat_user(chat_id=chat["id"], chat_source_id=chat_types["telegram"]["id"])
    upload = gen_chat_upload(user_id=other["id"], chat_type_id=chat_types["telegram"]["id"])

    with engine.cursor() as cur:
        insert_many(cur, "general.users", [owner, other])
        insert_many(cur, "general.chats", [chat])
        insert_many(cur, "general.chat_users", [chat_user])
        insert_many(
            cur,
            "general.user_chats",
            [{"user_id": owner["id"], "chat_id": chat["id"], "chat_user_id": chat_user["id"]}],
        )
        insert_many(cur, "general.chat_uploads", [upload])
    engine.commit()

    content = json.dumps(group).encode()
    minio_client.put_object(bucket, upload["file_path"], io.BytesIO(content), len(content))

    async def run():
        import niquests

        from polarsen.cli.ingest import process_uploads
        from polarsen.s3_utils import get_s3_client

        async with niquests.AsyncSession() as session:
            with get_s3_client() as s3:
                return await process_uploads(session, aengine, s3)

    assert loop.run_until_complete(run()) == []
    messages = fetch_one(engine, "SELECT count(*) AS count FROM general.chat_messages")
    pending = fetch_one(engine, "SELECT count(*) AS count FROM general.chat_uploads WHERE processed_at IS NULL")
    assert messages is not None and messages["count"] == 0
    assert pending is not None and pending["count"] == 0


def test_process_uploads_serializes_concurrent_new_chat(engine, minio_client: Minio, loop, monkeypatch):
    bucket = os.getenv("CHAT_UPLOADS_S3_BUCKET", "test-bucket")
    os.environ["CHAT_UPLOADS_S3_BUCKET"] = bucket
    env.CHAT_UPLOADS_S3_BUCKET = bucket
    if not minio_client.bucket_exists(bucket):
        minio_client.make_bucket(bucket)

    chat_types = load_chat_types(engine, as_dict=True)
    owner = gen_user(telegram_id=f"owner-{Fake.id()}")
    other = gen_user(telegram_id=f"other-{Fake.id()}")
    internal_code = str(Fake.id())

    def group_for(prefix: str) -> dict:
        group = gen_telegram_group()
        group["id"] = int(internal_code)
        for index, message in enumerate(group["messages"]):
            message["text"] = f"{prefix} message {index}"
            message["text_entities"] = [{"type": "plain", "text": message["text"]}]
        return group

    owner_group = group_for("owner")
    other_group = group_for("other")
    owner_content = json.dumps(owner_group).encode()
    other_content = json.dumps(other_group).encode()

    owner_upload = gen_chat_upload(user_id=owner["id"], chat_type_id=chat_types["telegram"]["id"])
    other_upload = gen_chat_upload(user_id=other["id"], chat_type_id=chat_types["telegram"]["id"])
    owner_upload["created_at"] = dt.datetime(2024, 1, 1, 10, 0, 0)
    other_upload["created_at"] = dt.datetime(2024, 1, 1, 10, 0, 1)

    with engine.cursor() as cur:
        insert_many(cur, "general.users", [owner, other])
        insert_many(cur, "general.chat_uploads", [owner_upload, other_upload])
    engine.commit()
    minio_client.put_object(bucket, owner_upload["file_path"], io.BytesIO(owner_content), len(owner_content))
    minio_client.put_object(bucket, other_upload["file_path"], io.BytesIO(other_content), len(other_content))

    pg_kwargs = {
        "database": os.environ["PG_DATABASE"],
        "user": os.environ["PG_USER"],
        "password": os.environ["PG_PASSWORD"],
        "host": os.environ["PG_HOST"],
        "port": 5432,
    }

    async def run():
        import niquests

        from polarsen.cli import ingest
        from polarsen.s3_utils import get_s3_client

        conn_owner = await asyncpg.connect(**pg_kwargs)
        conn_other = await asyncpg.connect(**pg_kwargs)
        original_get_ids = ingest.DbChat.get_ids
        calls = 0
        owner_holds_lock = asyncio.Event()
        other_started = asyncio.Event()

        async def coordinated_get_ids(conn, internal_codes):
            nonlocal calls
            calls += 1
            if calls == 1:
                # The owner holds the advisory lock here: the check runs after it is taken.
                owner_holds_lock.set()
                await other_started.wait()
            return await original_get_ids(conn, internal_codes)

        monkeypatch.setattr(ingest.DbChat, "get_ids", coordinated_get_ids)

        async def worker(conn):
            async with niquests.AsyncSession() as session:
                with get_s3_client() as s3:
                    async with conn.transaction():
                        return await ingest.process_uploads(session, conn, s3, limit=1)

        try:
            owner_task = asyncio.create_task(worker(conn_owner))
            await owner_holds_lock.wait()
            other_task = asyncio.create_task(worker(conn_other))
            other_started.set()
            owner_chats = await owner_task
            other_chats = await other_task
        finally:
            await conn_owner.close()
            await conn_other.close()
        return owner_chats, other_chats

    owner_chats, other_chats = loop.run_until_complete(run())

    assert len(owner_chats) == 1
    assert other_chats == []
    chat_row = fetch_one(engine, "SELECT id, created_by FROM general.chats WHERE internal_code = %s", internal_code)
    assert chat_row is not None
    assert chat_row["created_by"] == owner["id"]
    chat_id = chat_row["id"]
    messages = fetch_all(engine, "SELECT message FROM general.chat_messages WHERE chat_id = %s", chat_id)
    assert {row["message"] for row in messages} == {f"owner message {index}" for index in range(3)}
    uploads = fetch_all(engine, "SELECT user_id, chat_id, processed_at FROM general.chat_uploads")
    by_user = {row["user_id"]: row for row in uploads}
    assert by_user[owner["id"]]["chat_id"] == chat_id
    assert by_user[owner["id"]]["processed_at"] is not None
    assert by_user[other["id"]]["chat_id"] == chat_id
    assert by_user[other["id"]]["processed_at"] is not None
