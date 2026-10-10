import datetime as dt
from typing import Literal, NotRequired, TypedDict

from polarsen.common.utils import AISource
from polarsen.db import UserMeta

__all__ = ("AIModel", "AskQuestion", "Chat", "ChatType", "ChatUpload", "EmbeddingResult", "NewUser", "Status", "User")


class NewUser(TypedDict):
    telegram_id: str
    first_name: NotRequired[str | None]
    last_name: NotRequired[str | None]
    meta: NotRequired[UserMeta | None]
    api_keys: NotRequired[dict[AISource, str] | None]


class Chat(TypedDict):
    id: int
    name: str
    cutoff_date: dt.date | None


class EmbeddingResult(TypedDict):
    summary: str
    title: str
    day: dt.date
    distance: float


class ChatUpload(TypedDict):
    file_id: int
    filename: str
    file_path: str
    created_at: dt.datetime
    chat_type: str
    processed_at: dt.datetime | None


class User(TypedDict):
    id: int
    first_name: str | None
    last_name: str | None
    chats: list[Chat]
    api_keys: dict[AISource, str] | None
    meta: dict | None
    uploads: list[ChatUpload]


class AskQuestion(TypedDict):
    response: str
    results: list[EmbeddingResult]
    question_id: int


class AIModel(TypedDict):
    name: str
    source: AISource


class Status(TypedDict):
    status: str
    message: str | None


ChatType = Literal["telegram"]
