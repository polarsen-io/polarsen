from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass
from typing import Literal, Iterable, TypedDict, NotRequired

import asyncpg
from tracktolib.pg import insert_many, PGConflictQuery, insert_returning, insert_one, update_one

from .utils import TableID

__all__ = (
    "GroupMethod",
    "MessageGroup",
    "MessageGroupChat",
    "MISTRAL_EMBED_VECTOR_SIZE",
    "get_unique_identifier",
    "MistralGroupEmbeddings",
    "Requests",
    "RequestType",
    "UsageToken",
)


class UsageToken(TypedDict):
    total: int
    input: int
    output: int
    cached: NotRequired[int]


MISTRAL_EMBED_VECTOR_SIZE = 1024


@dataclass
class GroupMethod(TableID):
    name: str
    internal_code: str
    meta: dict | None = None

    @staticmethod
    async def bulk_save(conn: asyncpg.Connection, methods: list["GroupMethod"]):
        await insert_many(
            conn,
            "ai.message_group_methods",
            [m.data for m in methods],
            on_conflict=PGConflictQuery(keys=["internal_code"]),
        )

    @staticmethod
    async def get_ids(conn: asyncpg.Connection, internal_codes: list[str]) -> dict[str, int]:
        query = "SELECT id, internal_code FROM ai.message_group_methods WHERE internal_code = ANY($1)"
        _data = await conn.fetch(query, internal_codes)
        return {x["internal_code"]: x["id"] for x in _data}

    async def upsert(self, conn: asyncpg.Connection) -> int:
        _data = super().data
        _id = await insert_returning(
            conn, "ai.message_group_methods", _data, returning="id", on_conflict="ON CONFLICT DO NOTHING"
        )
        if _id is None:
            _id = await conn.fetchval(
                "SELECT id FROM ai.message_group_methods WHERE internal_code = $1", self.internal_code
            )
            if _id is None:
                raise ValueError(f"Failed to upsert group {self.internal_code!r}")

        self._id = _id
        return _id


def get_unique_identifier(ids: list[int], meta: str | None = None) -> str:
    id_encoded = (meta + str(ids) if meta is not None else str(ids)).encode()
    hash_object = hashlib.md5(id_encoded)
    return hash_object.hexdigest()


@dataclass
class MessageGroup(TableID):
    chat_id: int
    group_method_id: int
    internal_code: str
    summary: str | None = None
    title: str | None = None
    meta: dict | None = None
    run_id: uuid.UUID | None = None

    @staticmethod
    async def set_is_processing(conn: asyncpg.Connection, group_ids: list[int]) -> None:
        """Set the chats as processing in the meta field."""
        await conn.execute(
            """
            UPDATE ai.message_groups
            SET meta = COALESCE(meta, '{}') || jsonb_build_object(
                    'embeddings_started_at', now(),
                    'embeddings_status', 'processing'
            )
            WHERE id = ANY($1)
            """,
            group_ids,
        )

    @staticmethod
    async def set_processing_error(conn: asyncpg.Connection, group_ids: list[int], message: str | None = None) -> None:
        """Set the chats as error in the meta field."""
        await conn.execute(
            """
            UPDATE ai.message_groups
            SET meta = COALESCE(meta, '{}') || jsonb_build_object(
                    'embeddings_error_at', now(),
                    'embeddings_status', 'error',
                    'embeddings_error_message', $2::text
            )
            WHERE id = ANY($1)
            """,
            group_ids,
            message,
        )

    @staticmethod
    async def set_processing_done(conn: asyncpg.Connection, group_ids: list[int]) -> None:
        """Set the chats as done in the meta field."""
        await conn.execute(
            """
            UPDATE ai.message_groups
            SET meta = COALESCE(meta, '{}') || jsonb_build_object(
                    'embeddings_done_at', now(),
                    'embeddings_status', 'done'
            ) - 'embeddings_error_at' - 'embeddings_error_message'
            WHERE id = ANY($1)
            """,
            group_ids,
        )

    @staticmethod
    async def reset_processing(conn: asyncpg.Connection, group_ids: list[int]) -> None:
        """Reset the processing status of the chats in the meta field."""
        await conn.execute(
            """
            UPDATE ai.message_groups
            SET meta = meta - 'embeddings_status'
            WHERE id = any($1)
            """,
            group_ids,
        )

    async def upsert(self, conn: asyncpg.Connection) -> int:
        _data = super().data
        _id = await insert_returning(
            conn,
            "ai.message_groups",
            _data,
            returning="id",
            on_conflict=PGConflictQuery(keys=["internal_code", "group_method_id"]),
        )
        if _id is None:
            _id = await conn.fetchval(
                "SELECT id FROM ai.message_group WHERE internal_code = $1 and group_method_id = $2",
                self.internal_code,
                self.group_method_id,
            )
            if _id is None:
                raise ValueError(f"Failed to upsert message group {self.internal_code:=}, {self.group_method_id=}")

        self._id = _id
        return _id


@dataclass
class MessageGroupChat(TableID):
    chat_id: int
    group_id: int
    msg_id: int

    @staticmethod
    async def bulk_save(conn: asyncpg.Connection, messages: Iterable["MessageGroupChat"]):
        _all_data = []
        for m in messages:
            _data = m.data
            _all_data.append(_data)
        await insert_many(
            conn,
            "ai.message_group_chats",
            _all_data,
            # on_conflict=PGConflictQuery(keys=['group_id', 'chat_id'])
            on_conflict="ON CONFLICT DO NOTHING",
        )


@dataclass
class MistralGroupEmbeddings(TableID):
    group_id: int
    embedding: list[float]
    last_processed_at: dt.datetime | None = None

    def __post_init__(self):
        _now = dt.datetime.now(dt.timezone.utc)
        self._created_at = _now
        self.last_processed_at = _now

    async def save(self, conn: asyncpg.Connection):
        data = self.data
        await insert_one(
            conn,
            "ai.mistral_group_embeddings",
            data,
            on_conflict="""
                         ON CONFLICT (group_id) DO UPDATE SET
                            embedding = EXCLUDED.embedding,
                            last_processed_at = EXCLUDED.last_processed_at
                         """,
        )

    @staticmethod
    async def bulk_save(conn: asyncpg.Connection, embeddings: list[MistralGroupEmbeddings]):
        await insert_many(
            conn,
            "ai.mistral_group_embeddings",
            [e.data for e in embeddings],
            on_conflict="""
                         ON CONFLICT (group_id) DO UPDATE SET
                            embedding = EXCLUDED.embedding,
                            last_processed_at = EXCLUDED.last_processed_at
                         """,
        )


RequestType = Literal["chat", "embedding", "completion"]


@dataclass
class Requests(TableID):
    request_type: RequestType
    total_tokens: int
    user_id: int
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    payload: dict | None = None
    meta: dict | None = None
    run_id: uuid.UUID | None = None

    @classmethod
    def load(
        cls,
        request_type: RequestType,
        token: UsageToken,
        user_id: int,
        payload: dict | None = None,
        meta: dict | None = None,
        run_id: uuid.UUID | None = None,
    ) -> Requests:
        return cls(
            request_type=request_type,
            total_tokens=token["total"],
            input_tokens=token["input"],
            output_tokens=token["output"],
            cached_tokens=token.get("cached", 0),
            payload=payload,
            meta=meta,
            run_id=run_id,
            user_id=user_id,
        )

    async def save(self, conn: asyncpg.Connection):
        data = self.data
        await insert_one(conn, "ai.requests", data)

    @staticmethod
    async def update(conn: asyncpg.Connection, data: dict):
        await update_one(conn, "ai.requests", data, keys=["run_id"], merge_keys=["meta"])
