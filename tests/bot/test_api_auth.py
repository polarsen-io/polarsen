import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import pytest

from polarsen.bot import data
from polarsen.bot.bot import _select_ai
from polarsen.bot.env import API_URI

USER_PAYLOAD = {
    "id": 1,
    "first_name": None,
    "last_name": None,
    "chats": [],
    "api_keys": None,
    "meta": None,
    "uploads": [],
}


async def _save_all_users() -> None:
    data._USERS[1] = data.User(telegram_id=1)
    try:
        await data.User.save_all_users()
    finally:
        data._USERS.clear()


async def _fetch_user() -> None:
    await data.User.fetch_user(1)


async def _fetch_save_user() -> None:
    await data.User.fetch_save_user({"telegram_id": "user1"})


async def _ask_question() -> None:
    await data.ask_question(chat_id=1, question="q", model="mistral-large-latest", api_key="key", user_id=1)


async def _give_feedback() -> None:
    await data.give_feedback(question_id=1, feedback="good")


async def _upload_chat() -> None:
    await data.upload_chat(
        "https://telegram.example/file", user_id=1, filename="chat.json", mime_type="application/json"
    )


async def _fetch_models() -> None:
    await _select_ai(data.User(telegram_id=1))


@pytest.fixture
def fake_api(monkeypatch):
    calls: list[tuple[str, str, dict[str, str]]] = []

    class FakeResponse:
        def __init__(self, payload: Any, content: bytes = b"", content_length: int | None = None):
            self._payload = payload
            self._content = content
            self.headers = {"Content-Length": str(content_length)} if content_length is not None else {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            return self._payload

        async def iter_content(self, chunk_size: int = -1):
            async def generate():
                yield self._content

            return generate()

    class FakeAsyncSession:
        response: FakeResponse

        def __init__(self, *args: Any, **kwargs: Any):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: Any):
            return False

        async def _record(self, method: str, url: str, **kwargs: Any):
            calls.append((method, url, kwargs.get("headers") or {}))
            return self.response

        async def get(self, url: str, **kwargs: Any):
            return await self._record("GET", url, **kwargs)

        async def post(self, url: str, **kwargs: Any):
            return await self._record("POST", url, **kwargs)

        async def patch(self, url: str, **kwargs: Any):
            return await self._record("PATCH", url, **kwargs)

    monkeypatch.setattr("polarsen.bot.data.AsyncSession", FakeAsyncSession)
    monkeypatch.setattr("polarsen.bot.bot.AsyncSession", FakeAsyncSession)
    return calls, FakeAsyncSession, FakeResponse


@pytest.mark.parametrize(
    "call,payload",
    [
        pytest.param(_save_all_users, {"status": "ok"}, id="save-all-users"),
        pytest.param(_fetch_user, USER_PAYLOAD, id="fetch-user"),
        pytest.param(_fetch_save_user, USER_PAYLOAD, id="fetch-save-user"),
        pytest.param(
            _ask_question,
            {"response": "answer", "results": [], "question_id": 1},
            id="ask-question",
        ),
        pytest.param(_give_feedback, {"status": "ok", "message": None}, id="give-feedback"),
        pytest.param(
            _upload_chat,
            {
                "file_id": 1,
                "filename": "chat.json",
                "file_path": "1/chat.json",
                "created_at": "2024-01-01T00:00:00",
                "chat_type": "Telegram",
                "processed_at": None,
            },
            id="upload-chat",
        ),
        pytest.param(_fetch_models, [{"name": "mistral-large-latest", "source": "mistral"}], id="fetch-models"),
    ],
)
def test_bot_api_calls_include_bearer_token(
    monkeypatch, fake_api, call: Callable[[], Coroutine[Any, Any, None]], payload: Any
):
    calls, session_cls, response_cls = fake_api
    monkeypatch.setenv("INTERNAL_API_TOKEN", "bot-secret")
    session_cls.response = response_cls(payload, content=b"data", content_length=4)

    asyncio.run(call())

    api_calls = [c for c in calls if c[1].startswith(API_URI)]
    assert api_calls
    for _, _, headers in api_calls:
        assert headers.get("Authorization") == "Bearer bot-secret"


def test_upload_chat_keeps_content_length_header(monkeypatch, fake_api):
    from polarsen.bot import bot

    calls, session_cls, response_cls = fake_api
    monkeypatch.setenv("INTERNAL_API_TOKEN", "bot-secret")
    session_cls.response = response_cls(
        {
            "file_id": 1,
            "filename": "chat.json",
            "file_path": "1/chat.json",
            "created_at": "2024-01-01T00:00:00",
            "chat_type": "Telegram",
            "processed_at": None,
        },
        content=b"data",
        content_length=4,
    )

    asyncio.run(
        bot.upload_chat("https://telegram.example/file", user_id=1, filename="chat.json", mime_type="application/json")
    )

    upload_calls = [c for c in calls if c[1] == f"{API_URI}/chats/upload"]
    assert upload_calls
    assert upload_calls[0][2]["X-Content-Length"] == "4"
    assert upload_calls[0][2]["Authorization"] == "Bearer bot-secret"
