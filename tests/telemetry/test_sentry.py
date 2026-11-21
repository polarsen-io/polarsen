import json

import pytest

from polarsen.telemetry import sentry

SECRET = "sk-live-xxx"


def _event():
    return {
        "event_id": "abc123",
        "request": {
            "url": f"https://generativelanguage.googleapis.com/v1/models?api_key={SECRET}&model=gemini",
            "method": "POST",
            "query_string": f"api_key={SECRET}&model=gemini",
            "headers": {
                "x-goog-api-key": SECRET,
                "content-type": "application/json",
                "accept": "application/json",
            },
            "env": {
                "HTTP_X_GOOG_API_KEY": SECRET,
                "HTTP_ACCEPT": "application/json",
                "PATH_INFO": "/v1/models",
            },
        },
    }


def test_secret_is_removed_from_serialized_event():
    scrubbed = sentry.before_send(_event(), {})

    assert SECRET not in json.dumps(scrubbed)


def test_non_secret_request_data_is_kept():
    scrubbed = sentry.before_send(_event(), {})
    request = scrubbed["request"]

    assert request["method"] == "POST"
    assert "generativelanguage.googleapis.com/v1/models" in request["url"]
    assert request["headers"]["content-type"] == "application/json"
    assert request["headers"]["accept"] == "application/json"
    assert "model=gemini" in request["query_string"]
    assert request["env"]["HTTP_ACCEPT"] == "application/json"
    assert request["env"]["PATH_INFO"] == "/v1/models"


@pytest.mark.parametrize(
    "header",
    ["x-goog-api-key", "authorization", "x-api-key", "proxy-authorization", "x-auth-token"],
)
def test_provider_secret_headers_are_redacted(header):
    event = _event()
    event["request"]["headers"] = {header: SECRET, "content-type": "application/json"}

    scrubbed = sentry.before_send(event, {})

    assert scrubbed["request"]["headers"][header] != SECRET
    assert scrubbed["request"]["headers"]["content-type"] == "application/json"


@pytest.mark.parametrize("param", ["api_key", "key", "token", "access_token", "client_secret"])
def test_secret_query_params_are_redacted(param):
    event = _event()
    event["request"]["query_string"] = f"{param}={SECRET}&model=gemini"
    event["request"]["url"] = f"https://example.com/v1/models?{param}={SECRET}&model=gemini"

    scrubbed = sentry.before_send(event, {})

    assert SECRET not in scrubbed["request"]["query_string"]
    assert SECRET not in scrubbed["request"]["url"]
    assert "model=gemini" in scrubbed["request"]["query_string"]
    assert "model=gemini" in scrubbed["request"]["url"]


def test_query_string_in_raw_environ_is_redacted():
    event = _event()
    event["request"]["env"]["QUERY_STRING"] = f"api_key={SECRET}&model=gemini"

    scrubbed = sentry.before_send(event, {})

    assert SECRET not in json.dumps(scrubbed)
    assert "model=gemini" in scrubbed["request"]["env"]["QUERY_STRING"]
    assert scrubbed["request"]["env"]["PATH_INFO"] == "/v1/models"


def _transaction_event():
    return {
        "type": "transaction",
        "transaction": "POST /v1/models",
        "spans": [
            {
                "span_id": "span1",
                "op": "http.client",
                "data": {
                    "http.query": f"api_key={SECRET}&model=gemini",
                    "url": f"https://generativelanguage.googleapis.com/v1/models?api_key={SECRET}&model=gemini",
                    "http.response.status_code": 200,
                },
            }
        ],
    }


def test_transaction_span_query_is_redacted_without_a_request():
    event = _transaction_event()

    scrubbed = sentry.before_send_transaction(event, {})

    assert SECRET not in json.dumps(scrubbed)
    data = scrubbed["spans"][0]["data"]
    assert "model=gemini" in data["http.query"]
    assert "generativelanguage.googleapis.com/v1/models" in data["url"]
    assert data["http.response.status_code"] == 200


def test_before_send_also_scrubs_spans():
    event = _transaction_event()

    scrubbed = sentry.before_send(event, {})

    assert SECRET not in json.dumps(scrubbed)
    assert scrubbed["transaction"] == "POST /v1/models"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("http.query", f"api_key={SECRET}&model=gemini"),
        ("url", f"https://example.com/v1/models?api_key={SECRET}&model=gemini"),
        ("http.url", f"https://example.com/v1/models?api_key={SECRET}&model=gemini"),
    ],
)
def test_span_url_and_query_keys_are_redacted(key, value):
    event = _transaction_event()
    event["spans"][0]["data"] = {key: value}

    scrubbed = sentry.before_send_transaction(event, {})

    assert SECRET not in scrubbed["spans"][0]["data"][key]
    assert "model=gemini" in scrubbed["spans"][0]["data"][key]


def test_headers_as_pairs_are_redacted():
    event = _event()
    event["request"]["headers"] = [("x-goog-api-key", SECRET), ("accept", "application/json")]

    scrubbed = sentry.before_send(event, {})

    assert [value for name, value in scrubbed["request"]["headers"] if name == "x-goog-api-key"] != [SECRET]
    assert ("accept", "application/json") in scrubbed["request"]["headers"]


def test_event_without_request_is_unchanged():
    event = {"message": "boom"}

    assert sentry.before_send(event, {}) is event


def test_init_sentry_disables_pii_and_installs_callbacks(monkeypatch):
    calls = []

    class FakeSentry:
        @staticmethod
        def init(**kwargs):
            calls.append(kwargs)

    monkeypatch.setenv("SENTRY_DSN", "https://public@sentry.example.com/1")
    monkeypatch.setattr(sentry, "sentry_sdk", FakeSentry)

    sentry.init_sentry()

    assert len(calls) == 1
    assert calls[0]["send_default_pii"] is False
    assert calls[0]["before_send"] is sentry.before_send
    assert calls[0]["before_send_transaction"] is sentry.before_send_transaction


def _nested_event():
    return {
        "event_id": "abc123",
        "extra": {
            "anything": {
                "http.query": f"api_key={SECRET}&model=gemini",
            }
        },
        "breadcrumbs": {
            "values": [
                {
                    "type": "http",
                    "data": {
                        "http.query": f"api_key={SECRET}&model=gemini",
                        "url": f"https://example.com/v1/models?api_key={SECRET}&model=gemini",
                    },
                }
            ]
        },
    }


def test_generic_pass_redacts_secret_outside_targeted_paths():
    scrubbed = sentry.before_send(_nested_event(), {})

    assert SECRET not in json.dumps(scrubbed)
    assert "model=gemini" in scrubbed["extra"]["anything"]["http.query"]
    assert "model=gemini" in scrubbed["breadcrumbs"]["values"][0]["data"]["http.query"]
    assert "example.com/v1/models" in scrubbed["breadcrumbs"]["values"][0]["data"]["url"]


def test_generic_pass_redacts_secret_named_keys_at_any_depth():
    event = {
        "extra": {
            "anything": {"authorization": SECRET, "note": "keep me"},
            "items": [{"x-goog-api-key": SECRET}, "api_key=" + SECRET + "&model=gemini"],
        },
        "transaction": "POST /v1/models",
    }

    scrubbed = sentry.before_send_transaction(event, {})

    assert SECRET not in json.dumps(scrubbed)
    assert scrubbed["extra"]["anything"]["note"] == "keep me"
    assert scrubbed["transaction"] == "POST /v1/models"
