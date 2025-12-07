import datetime as dt
from types import EllipsisType
from typing import TypedDict, NotRequired, Sequence, Mapping, Any, TypeIs, overload, Literal

import psycopg
from tracktolib.pg_sync import fetch_all

from .utils import Fake

__all__ = (
    "User",
    "gen_user",
    "ChatType",
    "gen_chat_type",
    "load_chat_types",
    "Chat",
    "gen_chat",
    "ChatUser",
    "gen_chat_user",
    "gen_telegram_group",
    "gen_chat_upload",
    "ChatUpload",
    "ChatMessage",
    "gen_chat_message",
    "MessageGroupMethod",
    "gen_message_group_method",
    "MessageGroup",
    "gen_message_group",
    "MessageGroupChat",
    "gen_message_group_chat",
    "DEFAULT_DATETIME",
)

type NotSet = EllipsisType
NOT_SET: NotSet = ...
DEFAULT_DATETIME = dt.datetime(2024, 1, 1, 10, 0, 0)


def is_not_set(value: Any) -> TypeIs[NotSet]:
    return value is NOT_SET


class User(TypedDict):
    id: int
    first_name: str | None
    last_name: str | None
    telegram_id: str | None
    internal_code: str
    api_keys: dict[str, str] | None
    meta: dict | None


def gen_user(telegram_id: str | None = None, api_keys: dict[str, str] | None = None, meta: dict | None = None) -> User:
    _id = Fake.id()
    return {
        "id": _id,
        "first_name": Fake.unique.first_name(),
        "last_name": Fake.unique.last_name(),
        "telegram_id": telegram_id,
        "internal_code": f"user-{_id}" if telegram_id is None else f"telegram_{telegram_id}",
        "api_keys": api_keys,
        "meta": meta,
    }


class ChatType(TypedDict):
    id: int
    name: str
    internal_code: str


def gen_chat_type(name: str | None = None) -> ChatType:
    _id = Fake.id()
    _name = name if name is not None else Fake.unique.name()
    return {"id": _id, "name": _name, "internal_code": _name.lower()}


@overload
def load_chat_types(engine: psycopg.Connection, as_dict: Literal[False] = False) -> Sequence[ChatType]: ...


@overload
def load_chat_types(engine: psycopg.Connection, as_dict: Literal[True]) -> Mapping[str, ChatType]: ...


def load_chat_types(engine: psycopg.Connection, as_dict: bool = False) -> Sequence[ChatType] | Mapping[str, ChatType]:
    data: list[Any] = fetch_all(engine, "SELECT id, name, internal_code FROM general.chat_types")
    if as_dict:
        return {x["internal_code"]: x for x in data}
    return [{"id": x["id"], "name": x["name"], "internal_code": x["internal_code"]} for x in data]


class Chat(TypedDict):
    id: int
    internal_code: str
    name: str
    created_by: int
    meta: NotRequired[dict | None]


def gen_chat(created_by: int, meta: dict | None | NotSet = NOT_SET) -> Chat:
    _id = Fake.id()
    _name = Fake.unique.name()
    _data: Chat = {
        "id": _id,
        "internal_code": _name.lower(),
        "name": _name,
        "created_by": created_by,
    }
    if not is_not_set(meta):
        _data["meta"] = meta
    return _data


class ChatUser(TypedDict):
    id: int
    username: str
    internal_code: str
    chat_id: int
    chat_source_id: int


def gen_chat_user(chat_id: int, chat_source_id: int) -> ChatUser:
    _id = Fake.id()
    return {
        "id": _id,
        "username": f"username-{_id}",
        "internal_code": f"code-{_id}",
        "chat_id": chat_id,
        "chat_source_id": chat_source_id,
    }


def gen_telegram_group():
    msg1_id, msg1_text = Fake.id(), Fake.sentence()
    msg2_id = Fake.id()
    msg3_id, msg3_text = Fake.id(), Fake.sentence()
    user1_id, user1_name = Fake.id(), Fake.unique.first_name()
    user2_id, user2_name = Fake.id(), Fake.unique.first_name()

    _group = {
        "name": "Test Group",
        "id": Fake.id(),
        "type": "private_group",
        "messages": [
            {
                "id": msg1_id,
                "type": "message",
                "date": "2022-01-23T12:21:31",
                "date_unixtime": "1642936891",
                "from": user1_name,
                "from_id": f"user{user1_id}",
                "photo": "(File not included. Change data exporting settings to download.)",
                "photo_file_size": 83654,
                "width": 1075,
                "height": 836,
                "text": msg1_text,
                "text_entities": [{"type": "plain", "text": msg1_text}],
            },
            {
                "id": msg2_id,
                "type": "message",
                "date": "2022-01-25T07:48:10",
                "date_unixtime": "1643093290",
                "from": user1_name,
                "from_id": f"user{user1_id}",
                "photo": "(File not included. Change data exporting settings to download.)",
                "photo_file_size": 22895,
                "width": 1014,
                "height": 206,
                "text": "",
                "text_entities": [],
            },
            {
                "id": msg3_id,
                "type": "message",
                "date": "2022-01-29T11:58:15",
                "date_unixtime": "1643453895",
                "from": user2_name,
                "from_id": f"user{user2_id}",
                "reply_to_message_id": msg1_id,
                "text": msg3_text,
                "text_entities": [{"type": "plain", "text": msg3_text}],
            },
        ],
    }
    return _group


class ChatUpload(TypedDict):
    id: int
    user_id: int
    chat_id: int | None
    filename: str
    mime_type: str
    file_path: str
    file_size: int
    md5: str
    processed_at: dt.datetime | None
    created_at: NotRequired[dt.datetime]
    chat_type_id: int | None


def gen_chat_upload(
    user_id: int,
    chat_id: int | None = None,
    filename: str | None = None,
    mime_type: str = "text/plain",
    processed_at: dt.datetime | None = None,
    chat_type_id: int = 0,
) -> ChatUpload:
    _id = Fake.id()
    return {
        "id": _id,
        "user_id": user_id,
        "chat_id": chat_id,
        "filename": filename if filename is not None else f"file-{_id}.txt",
        "mime_type": mime_type,
        "file_path": f"uploads/user_{user_id}/file_{_id}.txt",
        "file_size": Fake.random_int(min=1000, max=10_000),
        "md5": Fake.md5(),
        "processed_at": processed_at,
        "chat_type_id": chat_type_id,
    }


class ChatMessage(TypedDict):
    id: int
    chat_user_id: int
    chat_id: int
    message: str
    internal_code: str
    sent_at: dt.datetime


def gen_chat_message(
    chat_user_id: int, chat_id: int, message: str | None = None, sent_at: dt.datetime = DEFAULT_DATETIME
) -> ChatMessage:
    _id = Fake.id()
    return {
        "id": _id,
        "chat_user_id": chat_user_id,
        "chat_id": chat_id,
        "message": message if message is not None else Fake.sentence(),
        "internal_code": f"msg_{_id}",
        "sent_at": sent_at,
    }


class MessageGroupMethod(TypedDict):
    id: int
    name: str
    internal_code: str
    meta: NotRequired[dict | None]


def gen_message_group_method(name: str | None = None, internal_code: str | None = None) -> MessageGroupMethod:
    _id = Fake.id()
    _name = name if name is not None else f"Method {_id}"
    _code = internal_code if internal_code is not None else f"method_{_id}"
    return {
        "id": _id,
        "name": _name,
        "internal_code": _code,
    }


class MessageGroup(TypedDict):
    id: int
    chat_id: int
    group_method_id: int
    internal_code: str
    summary: str | None
    title: str | None
    meta: NotRequired[dict | None]


def gen_message_group(
    chat_id: int,
    group_method_id: int,
    summary: str | None = None,
    title: str | None = None,
    meta: dict | None | NotSet = NOT_SET,
) -> MessageGroup:
    _id = Fake.id()
    _data: MessageGroup = {
        "id": _id,
        "chat_id": chat_id,
        "group_method_id": group_method_id,
        "internal_code": f"group_{_id}",
        "summary": summary if summary is not None else Fake.sentence(),
        "title": title if title is not None else Fake.sentence(),
    }
    if not is_not_set(meta):
        _data["meta"] = meta
    return _data


class MessageGroupChat(TypedDict):
    group_id: int
    chat_id: int
    msg_id: int


def gen_message_group_chat(group_id: int, chat_id: int, msg_id: int) -> MessageGroupChat:
    return {
        "group_id": group_id,
        "chat_id": chat_id,
        "msg_id": msg_id,
    }
