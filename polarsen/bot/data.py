import typing
from dataclasses import dataclass, field
from enum import Enum
from typing import TypedDict

import niquests
from niquests import AsyncSession, HTTPError
from pydantic import TypeAdapter
from telegram import User as TelegramUser

from polarsen.common.utils import AISource, get_source_from_model, is_valid_source
from polarsen.logs import logs
from . import models
from .env import API_URI
from .intl import i18n

__all__ = ("User", "UserState", "ask_question", "give_feedback", "upload_chat")


class UserState(Enum):
    NORMAL = "normal"
    AWAITING_API_KEY = "awaiting_api_key"
    AWAITING_CHAT_UPLOAD = "awaiting_chat_upload"


class LastQuestion(TypedDict):
    question_id: int
    results: list[models.EmbeddingResult]
    response: str


@dataclass
class User:
    telegram_id: int
    first_name: str | None = None
    last_name: str | None = None
    lang_code: str = "en"
    id: int | None = None  # This will be set after loading the user from the API
    api_keys: dict[AISource, str] = field(default_factory=dict)
    chats: list[models.Chat] = field(default_factory=list)
    uploads: list[models.ChatUpload] = field(default_factory=list)
    selected_chat_id: int | None = None
    selected_model: str | None = None
    state: UserState = UserState.NORMAL
    last_question: LastQuestion | None = None
    pending_question: str | None = None  # Question to ask after API key is set

    @property
    def selected_chat_name(self) -> str | None:
        """Get the name of the selected chat, or empty if no chat is selected."""
        if self.selected_chat_id is None:
            return None
        for chat in self.chats:
            if chat["id"] == self.selected_chat_id:
                return chat["name"]
        return None

    @property
    def selected_model_source(self) -> AISource:
        """Get the source (mistral, openai, ...) of the selected model, or raise if no model is selected."""
        if self.selected_model is None:
            raise ValueError("No model selected")
        return get_source_from_model(self.selected_model)

    @property
    def selected_model_api_key(self) -> str | None:
        """Get the API key for the selected model, or None if no model is selected or no API key is set."""
        return self.api_keys.get(self.selected_model_source)

    @property
    def data(self) -> models.NewUser:
        meta = {}
        if self.selected_chat_id is not None:
            meta["selected_chat_id"] = self.selected_chat_id
        if self.selected_model is not None:
            meta["selected_model"] = self.selected_model
        return {
            "telegram_id": f"user{self.telegram_id}",
            "first_name": self.first_name,
            "last_name": self.last_name,
            "api_keys": self.api_keys,  # type: ignore
            "meta": meta,
        }

    async def save(self):
        logs.debug(f"Saving user {self.telegram_id!r}")
        _user = await self.fetch_save_user(self.data)
        self.id = _user["id"]

    @staticmethod
    async def save_all_users():
        data = [user.data for user in _USERS.values()]
        if not data:
            return
        async with AsyncSession() as session:
            resp = await session.post(f"{API_URI}/users/bulk", json=data)
            resp.raise_for_status()

    @classmethod
    async def load_user(cls, tg_user: TelegramUser):
        _cached_user = _USERS.get(tg_user.id)
        if _cached_user:
            return _cached_user

        user = cls(telegram_id=tg_user.id)
        await user.load()
        user.lang_code = tg_user.language_code or "en"
        _USERS[tg_user.id] = user
        return user

    async def load(self):
        logs.debug(f"Loading user {self.telegram_id!r}")
        data = await self.fetch_user(self.telegram_id)
        if data is None:
            await self.save()
            return
        meta = data.get("meta") or {}
        self.id = data.get("id")
        self.first_name = data.get("first_name")
        self.last_name = data.get("last_name")
        self.selected_chat_id = meta.get("selected_chat_id")
        self.selected_model = meta.get("selected_model")
        self.api_keys = {k: v for k, v in (data.get("api_keys") or {}).items() if is_valid_source(k)}
        self.chats = data.get("chats", [])
        self.uploads = data.get("uploads", [])

    def t(self, key: str, *args, **kwargs) -> str:
        return i18n.get(self.lang_code, key, *args, **kwargs)

    @staticmethod
    async def fetch_user(telegram_id: int) -> models.User | None:
        params = {
            "telegram_id": f"user{telegram_id}",
        }
        async with AsyncSession() as session:
            resp = await session.get(f"{API_URI}/users", params=params)
        _resp = _check_response(resp)
        return _UserAdapter.validate_python(_resp) if _resp is not None else None

    @staticmethod
    async def fetch_save_user(data: models.NewUser) -> models.User:
        async with AsyncSession() as session:
            resp = await session.post(f"{API_URI}/users", json=data)
        return _UserAdapter.validate_python(_check_response(resp))

    def set_last_question(self, question_id: int, results: list[models.EmbeddingResult], response: str):
        """Set the last question asked by the user."""
        self.last_question = LastQuestion(question_id=question_id, results=results, response=response)


_USERS: dict[int, User] = {}  # Cache for users loaded from the API

_UserAdapter = TypeAdapter(models.User)


def _check_response(resp: niquests.Response) -> dict:
    try:
        resp.raise_for_status()
    except HTTPError as e:
        logs.error(resp.json())
        raise e
    return resp.json()


_AskQuestionAdapter = TypeAdapter(models.AskQuestion)


async def ask_question(chat_id: int, question: str, model: str, user_id: int) -> models.AskQuestion:
    params = {"question": question, "model": model, "user_id": user_id}
    async with AsyncSession() as session:
        resp = await session.get(f"{API_URI}/chats/{chat_id}/ask", params=params)
    return _AskQuestionAdapter.validate_python(_check_response(resp))


_StatusTypeAdapter = TypeAdapter(models.Status)


async def give_feedback(question_id: int, feedback: str) -> models.Status:
    params = {"feedback": feedback}
    async with AsyncSession() as session:
        resp = await session.patch(f"{API_URI}/questions/{question_id}", params=params)
    return _StatusTypeAdapter.validate_python(_check_response(resp))


async def download_telegram_file(
    session: niquests.AsyncSession,
    url: str,
    chunk_size: int = -1,
) -> tuple[typing.AsyncIterable[bytes], int | None]:
    resp = await session.get(url, stream=True)
    content_length = resp.headers.get("Content-Length")
    resp.raise_for_status()

    # Parse content_length to int if available
    parsed_content_length = int(content_length) if content_length is not None else None

    async def chunk_generator():
        total_length = 0
        content = await resp.iter_content(chunk_size=chunk_size)
        async for chunk in content:
            total_length += len(chunk)
            yield chunk

    return chunk_generator(), parsed_content_length


_ChatUploadValidator = TypeAdapter(models.ChatUpload)


async def upload_chat(url: str, user_id: int, filename: str, mime_type: str) -> models.ChatUpload:
    """
    Upload a chat file to the server.
    """
    params = {
        "user_id": str(user_id),
        "filename": filename,
        "mime_type": mime_type,
        "chat_type": "telegram",
    }
    async with AsyncSession() as session:
        data, total_content_length = await download_telegram_file(session, url)
        headers = {"X-Content-Length": str(total_content_length)} if total_content_length is not None else None

        resp = await session.post(f"{API_URI}/chats/upload", data=data, headers=headers, params=params)

    return _ChatUploadValidator.validate_python(_check_response(resp))
