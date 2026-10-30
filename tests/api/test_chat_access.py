import json
from http import HTTPStatus

import pytest
from tracktolib.pg_sync import fetch_one, insert_many

from tests.data.db import gen_chat, gen_chat_user, gen_telegram_group, gen_user, load_chat_types
from tests.data.utils import Fake
from ..utils import generate_large_file


@pytest.fixture
def chat_access(engine):
    chat_types = load_chat_types(engine, as_dict=True)
    owner = gen_user(telegram_id=f"owner-{Fake.id()}")
    other = gen_user(telegram_id=f"other-{Fake.id()}")
    chat = gen_chat(created_by=owner["id"])
    chat_user = gen_chat_user(chat_id=chat["id"], chat_source_id=chat_types["telegram"]["id"])
    # get_telegram_chat_username matches users.telegram_id against chat_users.internal_code.
    owner["telegram_id"] = chat_user["internal_code"]
    user_chat = {
        "user_id": owner["id"],
        "chat_id": chat["id"],
        "chat_user_id": chat_user["id"],
    }

    with engine.cursor() as cur:
        insert_many(cur, "general.users", [owner, other])
        insert_many(cur, "general.chats", [chat])
        insert_many(cur, "general.chat_users", [chat_user])
        insert_many(cur, "general.user_chats", [user_chat])
    engine.commit()
    return owner, other, chat


class _FakeChatSession:
    async def ask_rag(self, **kwargs):
        return "Test answer", {"results": []}


@pytest.fixture
def fake_chat_session(monkeypatch):
    monkeypatch.setattr("polarsen.api.main.ChatSession.get_session", lambda **kwargs: _FakeChatSession())


def _ask(client, chat_id: int, user_id: int):
    return client.get(
        f"/chats/{chat_id}/ask",
        params={
            "api_key": "test-key",
            "question": "What happened?",
            "user_id": user_id,
            "model": "gpt-4.1",
        },
    )


def _upload(client, user_id: int, content: bytes, chat_id: int | None = None, declared_length: int | None = None):
    params = {
        "user_id": user_id,
        "filename": "chat.json",
        "mime_type": "application/json",
        "chat_type": "telegram",
    }
    if chat_id is not None:
        params["chat_id"] = chat_id
    return client.post(
        "/chats/upload",
        params=params,
        headers={"X-Content-Length": str(len(content) if declared_length is None else declared_length)},
        content=content,
    )


def test_ask_rejects_user_without_chat_access(client, chat_access, fake_chat_session):
    _, other, chat = chat_access

    resp = _ask(client, chat["id"], other["id"])

    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert resp.json()["error_code"] == "forbidden"


def test_ask_allows_chat_owner(client, chat_access, fake_chat_session):
    owner, _, chat = chat_access

    resp = _ask(client, chat["id"], owner["id"])

    assert resp.status_code == HTTPStatus.OK, resp.text
    assert resp.json()["response"] == "Test answer"


def test_upload_rejects_user_without_chat_access(client, chat_access):
    _, other, chat = chat_access

    resp = _upload(client, other["id"], generate_large_file(1024), chat_id=chat["id"])

    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert resp.json()["error_code"] == "forbidden"


def test_upload_allows_chat_owner(client, chat_access):
    owner, _, chat = chat_access

    resp = _upload(client, owner["id"], generate_large_file(1024), chat_id=chat["id"])

    assert resp.status_code == HTTPStatus.OK, resp.text


def test_upload_rejects_export_targeting_existing_chat_without_access(client, chat_access, engine):
    owner, other, _ = chat_access
    group = gen_telegram_group()
    target_chat = gen_chat(created_by=owner["id"])
    target_chat["internal_code"] = str(group["id"])

    with engine.cursor() as cur:
        insert_many(cur, "general.chats", [target_chat])
    engine.commit()

    resp = _upload(client, other["id"], json.dumps(group).encode())

    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert resp.json()["error_code"] == "forbidden"
    uploads = fetch_one(engine, "SELECT count(*) AS count FROM general.chat_uploads")
    messages = fetch_one(engine, "SELECT count(*) AS count FROM general.chat_messages")
    chats = fetch_one(engine, "SELECT count(*) AS count FROM general.chats")
    assert uploads is not None and uploads["count"] == 0
    assert messages is not None and messages["count"] == 0
    assert chats is not None and chats["count"] == 2


def test_upload_allows_export_targeting_new_chat(client, chat_access, engine):
    _, other, _ = chat_access
    group = gen_telegram_group()

    resp = _upload(client, other["id"], json.dumps(group).encode())

    assert resp.status_code == HTTPStatus.OK, resp.text
    uploads = fetch_one(engine, "SELECT count(*) AS count FROM general.chat_uploads")
    assert uploads is not None and uploads["count"] == 1


@pytest.mark.parametrize(
    "declared_length",
    [
        pytest.param(None, id="declared-length-matches-body"),
        pytest.param(10, id="declared-length-understates-body"),
    ],
)
def test_upload_rejects_export_over_size_limit(client, chat_access, engine, monkeypatch, declared_length):
    from polarsen import env

    owner, _, _ = chat_access
    monkeypatch.setattr(env, "CHAT_UPLOAD_MAX_BYTES", 1024)

    resp = _upload(client, owner["id"], generate_large_file(2048), declared_length=declared_length)

    assert resp.status_code == HTTPStatus.CONTENT_TOO_LARGE
    assert resp.json()["error_code"] == "invalid_file"
    uploads = fetch_one(engine, "SELECT count(*) AS count FROM general.chat_uploads")
    assert uploads is not None and uploads["count"] == 0
