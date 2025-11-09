import datetime as dt
from dataclasses import dataclass, field, asdict

import asyncpg
from rich.progress import track
from tracktolib.pg import insert_many, PGConflictQuery, insert_returning

from polarsen.logs import logs
from .utils import TableID

__all__ = (
    "DBChatUser",
    "DBChatMessage",
    "TelegramTextEntity",
    "TelegramMessage",
    "TelegramGroup",
    "CHAT_SOURCE_MAPPING",
    "DbChat",
    "ChatUpload",
)

CHAT_SOURCE_MAPPING = {
    "telegram": 0,
}


@dataclass
class DBChatUser(TableID):
    username: str
    internal_code: str
    chat_id: int
    chat_source_id: int
    _created_at: dt.datetime | None = field(default=None, init=False)
    # created_at: dt.datetime | None = None

    @property
    def data(self):
        _data = super().data
        return _data

    @staticmethod
    async def bulk_save(conn: asyncpg.Connection, users: list["DBChatUser"]):
        await insert_many(
            conn,
            "general.chat_users",
            [m.data for m in users],
            on_conflict=PGConflictQuery(keys=["chat_source_id", "internal_code"]),
        )

    @staticmethod
    async def get_ids(conn: asyncpg.Connection, internal_codes: list[str]) -> dict[str, int]:
        query = "SELECT id, internal_code FROM general.chat_users WHERE internal_code = ANY($1)"
        _data = await conn.fetch(query, internal_codes)
        return {x["internal_code"]: x["id"] for x in _data}


@dataclass
class DbChat(TableID):
    internal_code: str
    name: str
    created_by: int

    @property
    def data(self):
        _data = super().data
        return _data

    @staticmethod
    async def bulk_save(conn: asyncpg.Connection, chats: list["DbChat"]):
        await insert_many(
            conn, "general.chats", [m.data for m in chats], on_conflict=PGConflictQuery(keys=["internal_code"])
        )

    @staticmethod
    async def get_ids(conn: asyncpg.Connection, internal_codes: list[str]) -> dict[str, int]:
        query = "SELECT id, internal_code FROM general.chats WHERE internal_code = ANY($1)"
        _data = await conn.fetch(query, internal_codes)
        return {x["internal_code"]: x["id"] for x in _data}

    @staticmethod
    async def set_is_processing(conn: asyncpg.Connection, chat_ids: list[int]) -> None:
        await conn.execute(
            """
            UPDATE general.chats 
            SET meta = COALESCE(meta, '{}') || jsonb_build_object(
                    'processing_started_at', now(),
                    'status', 'processing'
            )
            WHERE id = ANY($1)
            """,
            chat_ids,
        )

    @staticmethod
    async def set_processing_error(conn: asyncpg.Connection, chat_ids: list[int], message: str | None = None) -> None:
        await conn.execute(
            """
            UPDATE general.chats 
            SET meta = COALESCE(meta, '{}') || jsonb_build_object(
                    'processing_error_at', now(),
                    'status', 'error',
                    'error_message', $2
            )
            WHERE id = ANY($1)
            """,
            chat_ids,
            message,
        )

    @staticmethod
    async def set_processing_done(conn: asyncpg.Connection, chat_ids: list[int]) -> None:
        await conn.execute(
            """
            UPDATE general.chats 
            SET meta = COALESCE(meta, '{}') || jsonb_build_object(
                    'processing_done_at', now(),
                    'status', 'done'
            ) - 'processing_error' - 'error_message'
            WHERE id = ANY($1)
            """,
            chat_ids,
        )

    @staticmethod
    async def reset_processing(conn: asyncpg.Connection, chat_ids: list[int]) -> None:
        await conn.execute(
            """
            UPDATE general.chats
            set meta = meta - 'status'
            where id = any($1)wo
            """,
            chat_ids,
        )


@dataclass
class DBChatMessage(TableID):
    chat_id: int
    chat_user_id: int
    sent_at: dt.datetime
    internal_code: str
    message: str
    reply_to_id: int | None = None

    @property
    def data(self):
        _data = super().data
        return _data

    @staticmethod
    async def bulk_save(conn: asyncpg.Connection, messages: list["DBChatMessage"]):
        await insert_many(
            conn,
            "general.chat_messages",
            [m.data for m in messages],
            on_conflict=PGConflictQuery(keys=["chat_user_id", "internal_code"]),
        )

    @staticmethod
    async def get_ids(conn: asyncpg.Connection, internal_codes: list[str]) -> dict[str, int]:
        query = "SELECT id, internal_code FROM general.chat_messages WHERE internal_code = ANY($1)"
        _data = await conn.fetch(query, internal_codes)
        return {x["internal_code"]: x["id"] for x in _data}


@dataclass
class TelegramTextEntity:
    type: str
    text: str
    user_id: int | None = None
    document_id: str | None = None


def _fmt_text(text: str | dict | list[str | dict]) -> str:
    """
    Format text with entities
    """
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        return " ".join([_fmt_text(t) for t in text])

    if not isinstance(text, dict):
        raise ValueError(f"Invalid text format: {text}")

    _text = text.get("text")
    if not isinstance(_text, str):
        raise ValueError(f"Invalid text format: {_text}")
    return _text


@dataclass
class TelegramMessage:
    chat_id: int
    message_id: int
    message_type: str
    message_date: dt.datetime
    from_user_id: int
    from_user: str
    text: str
    text_entities: list[TelegramTextEntity] = field(default_factory=list)
    reply_to_message_id: int | None = None

    @property
    def data(self):
        return asdict(self)

    @classmethod
    def load(cls, chat_id: int, msg: dict):
        if msg.get("action") is not None:
            return

        try:
            _text = _fmt_text(msg["text"])
            if _text is None:
                return

            return cls(
                chat_id=chat_id,
                message_id=msg["id"],
                message_type=msg["type"],
                message_date=dt.datetime.fromisoformat(msg["date"]),
                from_user_id=msg["from_id"],
                from_user=msg["from"],
                text=_text,
                text_entities=[TelegramTextEntity(**te) for te in msg["text_entities"]],
                reply_to_message_id=msg.get("reply_to_message_id"),
            )
        except (KeyError, TypeError) as e:
            import pprint

            pprint.pprint(msg)
            raise e

    def to_db_message(self, chat_id: int, chat_user_id: int, reply_to_chat_id: int | None = None) -> DBChatMessage:
        return DBChatMessage(
            chat_id=chat_id,
            internal_code=str(self.message_id),
            sent_at=self.message_date,
            message=self.text,
            chat_user_id=chat_user_id,
            reply_to_id=reply_to_chat_id,
        )

    def to_db_user(self, chat_id: int) -> DBChatUser:
        return DBChatUser(
            chat_id=chat_id,
            internal_code=str(self.from_user_id),
            username=self.from_user,
            chat_source_id=CHAT_SOURCE_MAPPING["telegram"],
        )


@dataclass
class TelegramGroup:
    name: str
    group_type: str
    group_id: int
    messages: list[TelegramMessage] = field(default_factory=list)

    @classmethod
    def load(cls, group: dict, *, show_progress: bool = False):
        messages = []
        nb_skipped = 0
        for msg in track(group.pop("messages"), disable=not show_progress):
            _msg = TelegramMessage.load(chat_id=msg["id"], msg=msg)
            if _msg is not None:
                messages.append(_msg)
            else:
                nb_skipped += 1

        if nb_skipped > 0:
            logs.warning(f"Skipped {nb_skipped} messages")

        try:
            return cls(name=group["name"], group_type=group["type"], group_id=group["id"], messages=messages)
        except KeyError as e:
            import pprint

            pprint.pprint(group)
            raise e

    def to_db_chat(self, created_by: int) -> DbChat:
        return DbChat(internal_code=str(self.group_id), name=self.name, created_by=created_by)

    async def save(self, conn: asyncpg.Connection, created_by: int) -> int:
        """
        Save the group, its users and messages to the database.
        Return the chat ID.
        """
        # Chat
        db_chat = self.to_db_chat(created_by=created_by)
        await DbChat.bulk_save(conn, [db_chat])
        chat_ids = await DbChat.get_ids(conn, [db_chat.internal_code])
        chat_id = chat_ids[str(self.group_id)]
        # Chat users
        chat_users = {m.from_user_id: m.to_db_user(chat_id=chat_id) for m in self.messages if m is not None}
        if not chat_users:
            logs.warning("No chat users to save, skipping")
            return chat_id
        logs.info(f"Saving {len(chat_users)} chat users")
        await DBChatUser.bulk_save(conn, list(chat_users.values()))
        db_users = await DBChatUser.get_ids(conn, [x.internal_code for x in chat_users.values()])
        chat_user_ids = {m.internal_code: db_users[m.internal_code] for m in chat_users.values()}
        # Chat messages
        messages, response_messages = [], []
        for m in self.messages:
            if m is None:
                continue
            if m.reply_to_message_id is None:
                messages.append(m.to_db_message(chat_user_id=chat_user_ids[str(m.from_user_id)], chat_id=chat_id))
            else:
                response_messages.append(m)
        logs.info(f"Saving {len(messages)} chat messages")
        await DBChatMessage.bulk_save(conn, messages)

        _msg_internal_codes = {m.internal_code for m in messages} | {str(m.chat_id) for m in response_messages}
        message_ids = await DBChatMessage.get_ids(conn, list(_msg_internal_codes))

        reply_messages = [
            m.to_db_message(
                chat_user_id=chat_user_ids[str(m.from_user_id)],
                chat_id=chat_id,
                reply_to_chat_id=message_ids[str(m.reply_to_message_id)],
            )
            for m in response_messages
            if message_ids.get(str(m.reply_to_message_id)) is not None
        ]
        if reply_messages:
            logs.info(f"Saving {len(reply_messages)} reply chat messages")
            await DBChatMessage.bulk_save(conn, reply_messages)

        return chat_id


@dataclass
class ChatUpload(TableID):
    user_id: int
    filename: str
    md5: str
    mime_type: str
    file_size: int | None
    file_path: str
    chat_type_id: int
    processed_at: dt.datetime | None = None

    @property
    def data(self) -> dict:
        return {
            "user_id": self.user_id,
            "filename": self.filename,
            "md5": self.md5,
            "mime_type": self.mime_type,
            "file_size": self.file_size,
            "file_path": self.file_path,
            "chat_type_id": self.chat_type_id,
        }

    async def save(self, conn: asyncpg.Connection) -> int:
        _data = await insert_returning(conn, "general.chat_uploads", self.data, returning=["id", "created_at"])
        if _data is None:
            raise ValueError(f"Failed to save chat upload {self.filename!r} for user {self.user_id}")
        self._id = _data["id"]
        self._created_at = _data["created_at"]
        return _data["id"]

    @staticmethod
    async def mark_processed(conn: asyncpg.Connection, chat_id: int, upload_id: int) -> None:
        await conn.execute(
            """
            UPDATE general.chat_uploads 
            SET processed_at = NOW(),
                chat_id = $2
            WHERE id = $1
                """,
            upload_id,
            chat_id,
        )
