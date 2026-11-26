import pytest

from polarsen.telemetry.sentry import scrub_event

ASK_URL = "http://api:5050/chats/1/ask?api_key=sk-secret&question=hello"


@pytest.mark.parametrize(
    "event,expected",
    [
        pytest.param(
            {"request": {"url": ASK_URL, "query_string": "api_key=sk-secret", "data": {"api_keys": {"x": "k"}}}},
            {"request": {"url": "http://api:5050/chats/1/ask"}},
            id="request",
        ),
        pytest.param(
            {"breadcrumbs": {"values": [{"data": {"url": ASK_URL, "http.query": "api_key=sk-secret"}}]}},
            {"breadcrumbs": {"values": [{"data": {"url": "http://api:5050/chats/1/ask"}}]}},
            id="http-breadcrumb",
        ),
        pytest.param({"message": "boom"}, {"message": "boom"}, id="no-request"),
    ],
)
def test_scrub_event(event, expected):
    assert scrub_event(event, {}) == expected
