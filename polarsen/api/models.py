import datetime as dt
from typing import TypedDict, Literal

from pydantic import BaseModel

from polarsen.common.utils import AISource
from .data import UserMeta

__all__ = ("NewUser", "Chat", "User", "EmbeddingResult", "AskQuestion", "AIModel", "Status", "ChatType", "ChatUpload")


class NewUser(BaseModel):
    telegram_id: str
    first_name: str | None = None
    last_name: str | None = None
    meta: UserMeta | None = None
    api_keys: dict[AISource, str] | None = None
    # password: str


class Chat(TypedDict):
    id: int
    name: str
    cutoff_date: dt.date | None


class User(BaseModel):
    id: int
    first_name: str | None
    last_name: str | None
    chats: list[Chat]
    api_keys: dict[AISource, str] | None
    meta: dict | None


class EmbeddingResult(TypedDict):
    summary: str
    title: str
    day: dt.date
    distance: float


class AskQuestion(BaseModel):
    response: str
    results: list[EmbeddingResult]
    question_id: int


class AIModel(BaseModel):
    name: str
    source: AISource


class Status(BaseModel):
    status: str
    message: str | None


class ChatUpload(BaseModel):
    file_id: int
    file_path: str


ChatType = Literal["telegram"]
