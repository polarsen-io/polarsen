import json
import re
from http import HTTPStatus

import pytest
from tracktolib.pg_sync import insert_many, fetch_one

from tests.data.db import gen_user, gen_chat_upload, load_chat_types
from .conftest import USERS
from ..utils import generate_large_file

FILE_1MB = generate_large_file(1 * 1024 * 1024)
FILE_6MB = generate_large_file(6 * 1024 * 1024)


PUBLIC_ROUTES = {"/version", "/health"}


def _internal_routes():
    from fastapi.routing import APIRoute

    from polarsen.api.main import app

    return [
        pytest.param(method, re.sub(r"{\w+}", "1", route.path), id=f"{method} {route.path}")
        for route in app.routes
        if isinstance(route, APIRoute) and route.path not in PUBLIC_ROUTES
        for method in sorted(route.methods)
    ]


class TestAuth:
    @pytest.mark.parametrize("method,path", _internal_routes())
    def test_rejects_missing_token(self, anon_client, method, path):
        resp = anon_client.request(method, path)
        assert resp.status_code == HTTPStatus.UNAUTHORIZED

    @pytest.mark.parametrize(
        "headers,expected",
        [
            pytest.param({"Authorization": "Bearer wrong-token"}, HTTPStatus.UNAUTHORIZED, id="wrong-token"),
            pytest.param({"Authorization": "Basic dXNlcjpwYXNz"}, HTTPStatus.UNAUTHORIZED, id="wrong-scheme"),
            pytest.param({}, HTTPStatus.OK, id="valid-token"),
        ],
    )
    def test_token(self, client, headers, expected):
        assert client.get("/models", headers=headers).status_code == expected

    def test_reports_missing_server_token(self, anon_client, monkeypatch):
        monkeypatch.delenv("INTERNAL_API_TOKEN")
        resp = anon_client.get("/models", headers={"Authorization": "Bearer anything"})
        assert resp.status_code == HTTPStatus.INTERNAL_SERVER_ERROR

    @pytest.mark.parametrize("path", [pytest.param(p, id=p) for p in sorted(PUBLIC_ROUTES)])
    def test_public_routes_need_no_token(self, anon_client, path):
        assert anon_client.get(path).status_code == HTTPStatus.OK


class TestUpload:
    @pytest.fixture(scope="function", autouse=True)
    def _setup(self, engine):
        with engine.cursor() as cur:
            insert_many(cur, "general.users", USERS)
        engine.commit()
        yield

    @pytest.mark.parametrize(
        "params,content",
        [
            pytest.param(
                {
                    "user_id": USERS[0]["id"],
                    "filename": "toto.txt",
                    "mime_type": "application/json",
                    "chat_type": "telegram",
                },
                FILE_6MB,
                id="large-file",
            ),
            pytest.param(
                {
                    "user_id": USERS[0]["id"],
                    "filename": "toto.txt",
                    "mime_type": "application/json",
                    "chat_type": "telegram",
                },
                FILE_1MB,
                id="small-file",
            ),
        ],
    )
    def test_upload(self, client, params, content, minio_client, engine):
        from polarsen import env

        headers = {"X-Content-Length": str(len(content))}
        resp_ctx = client.stream("POST", "/chats/upload", headers=headers, params=params, content=content)
        payload_resp = ""
        with resp_ctx as resp:
            assert resp.status_code == HTTPStatus.OK
            for text in resp.iter_text():
                payload_resp += text

        resp_data = json.loads(payload_resp)
        s3_files = minio_client.list_objects(bucket_name=env.CHAT_UPLOADS_S3_BUCKET, prefix=resp_data["file_path"])
        assert len(list(s3_files)) == 1
        assert fetch_one(engine, "SELECT * from general.chat_uploads WHERE id = %s", resp_data["file_id"])


class TestUsers:
    _default_user = {
        "telegram_id": "123456789",
    }

    @pytest.fixture(scope="function")
    def telegram_user(self):
        return gen_user(telegram_id=self._default_user["telegram_id"])

    @pytest.fixture(scope="function")
    def telegram_uploads(self, telegram_user, engine):
        chat_types = load_chat_types(engine, as_dict=True)
        return [
            gen_chat_upload(
                user_id=telegram_user["id"],
                chat_type_id=chat_types["telegram"]["id"],
            )
        ]

    @pytest.fixture(scope="function")
    def add_user(self, engine, telegram_user):
        with engine.cursor() as cur:
            insert_many(cur, "general.users", [telegram_user])
        engine.commit()

    @pytest.fixture(scope="function")
    def add_uploads(self, engine, telegram_uploads):
        with engine.cursor() as cur:
            insert_many(cur, "general.chat_uploads", telegram_uploads)
        engine.commit()

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(
                {**_default_user},
                id="basic-user",
            ),
            pytest.param(
                {
                    **_default_user,
                    "first_name": "Jane",
                    "last_name": "Smith",
                    "api_keys": {"openai": "sk-test123"},
                    "meta": {"selected_model": "test"},
                },
                id="user-with-api-keys-and-meta",
            ),
        ],
    )
    def test_create_user(self, client, payload, engine):
        resp = client.post("/users", json=payload)
        assert resp.status_code == HTTPStatus.OK, resp.text
        resp_data = resp.json()

        fields = list(payload.keys())
        fields_str = ", ".join(fields)

        # Verify user was created in database
        user_in_db = fetch_one(engine, f"SELECT {fields_str} from general.users WHERE id = %s", resp_data["id"])
        assert user_in_db is not None

        # Direct comparison of payload with database result
        assert payload == dict(user_in_db), f"Payload mismatch: expected {payload}, got {dict(user_in_db)}"

    @pytest.mark.usefixtures("add_user")
    def test_user_exists(self, client, engine, telegram_user):
        resp = client.post("/users", json={"telegram_id": telegram_user["telegram_id"]})
        assert resp.status_code == HTTPStatus.OK, resp.text
        resp_data = resp.json()
        assert resp_data == {
            "id": telegram_user["id"],
            "first_name": telegram_user["first_name"],
            "last_name": telegram_user["last_name"],
            # 'telegram_id': telegram_user['telegram_id'],
            # 'internal_code': telegram_user['internal_code'],
            "api_keys": telegram_user["api_keys"],
            "meta": telegram_user["meta"],
            "chats": [],
            "uploads": [],
        }

    @pytest.mark.usefixtures("add_user", "add_uploads")
    def test_get_users(self, client, telegram_user, telegram_uploads):
        resp = client.post("/users", json={"telegram_id": telegram_user["telegram_id"]})
        assert resp.status_code == HTTPStatus.OK, resp.text

        resp_data = resp.json()
        for _upload in resp_data["uploads"]:
            assert _upload.pop("created_at") is not None
        assert resp_data == {
            "id": telegram_user["id"],
            "first_name": telegram_user["first_name"],
            "last_name": telegram_user["last_name"],
            "api_keys": telegram_user["api_keys"],
            "meta": telegram_user["meta"],
            "chats": [],
            "uploads": [
                {
                    "chat_type": "Telegram",
                    "file_id": telegram_uploads[0]["id"],
                    "filename": telegram_uploads[0]["filename"],
                    "file_path": telegram_uploads[0]["file_path"],
                    "processed_at": None,
                }
            ],
        }
