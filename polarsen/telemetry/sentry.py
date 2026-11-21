import os
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    import sentry_sdk
    from sentry_sdk import capture_exception as _capture_exception
except ImportError:
    sentry_sdk = None
    _capture_exception = None

capture_exception = None

REDACTED = "[Filtered]"

# Query parameter names that carry credentials. Names are compared after
# lowercasing and replacing "-" with "_"; see _is_secret_query_param.
_SECRET_QUERY_PARAMS = frozenset(
    {
        "api_key",
        "apikey",
        "auth",
        "client_secret",
        "id_token",
        "key",
        "password",
        "passwd",
        "refresh_token",
        "secret",
        "sig",
        "signature",
        "token",
        "access_token",
    }
)

# Substrings that mark a header or WSGI environ entry as secret-bearing.
# Names are compared after lowercasing and replacing "-" with "_".
_SECRET_HEADER_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "signature",
    "token",
)


def _is_secret_query_param(name: str) -> bool:
    normalized = name.strip().lower().replace("-", "_")
    if normalized in _SECRET_QUERY_PARAMS:
        return True
    return normalized.endswith(("_key", "_token", "_secret", "_password", "_signature"))


def _is_secret_header(name: str) -> bool:
    normalized = name.strip().lower()
    normalized = normalized.removeprefix("http_")
    normalized = normalized.replace("-", "_")
    if normalized == "key" or normalized.endswith("_key"):
        return True
    return any(marker in normalized for marker in _SECRET_HEADER_MARKERS)


def _redact_pairs(pairs: Iterable[tuple[str, Any]]) -> list[tuple[str, Any]]:
    return [(name, REDACTED if _is_secret_query_param(name) else value) for name, value in pairs]


def _scrub_query_string(query: Any) -> Any:
    if isinstance(query, bytes):
        return _scrub_query_string(query.decode("utf-8", "replace")).encode()
    if isinstance(query, str):
        pairs = parse_qsl(query, keep_blank_values=True)
        if not pairs:
            return query
        return urlencode(_redact_pairs(pairs))
    if isinstance(query, Mapping):
        return {name: REDACTED if _is_secret_query_param(str(name)) else value for name, value in query.items()}
    if isinstance(query, (list, tuple)):
        return _redact_pairs([(str(name), value) for name, value in query])
    return query


def _scrub_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not pairs:
        return url
    query = urlencode(_redact_pairs(pairs))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _scrub_headers(headers: Any) -> Any:
    if isinstance(headers, Mapping):
        return {name: REDACTED if _is_secret_header(str(name)) else value for name, value in headers.items()}
    if isinstance(headers, (list, tuple)):
        return [(name, REDACTED if _is_secret_header(str(name)) else value) for name, value in headers]
    return headers


def _is_query_string_key(name: str) -> bool:
    normalized = name.strip().lower().replace("-", "_")
    return normalized.endswith("query_string")


def _scrub_environ_value(name: Any, value: Any) -> Any:
    if _is_query_string_key(str(name)):
        return _scrub_query_string(value)
    if _is_secret_header(str(name)):
        return REDACTED
    return value


def _scrub_environ(environ: Any) -> Any:
    if isinstance(environ, Mapping):
        return {name: _scrub_environ_value(name, value) for name, value in environ.items()}
    if isinstance(environ, (list, tuple)):
        return [(name, _scrub_environ_value(name, value)) for name, value in environ]
    return environ


def _is_url_key(name: str) -> bool:
    normalized = name.strip().lower().replace("-", "_")
    return normalized.endswith(("url", "query", "query_string"))


def _scrub_url_like(value: Any) -> Any:
    """Scrub a value that may be a full URL or a bare query string."""
    if isinstance(value, str):
        try:
            parts = urlsplit(value)
        except ValueError:
            return _scrub_query_string(value)
        if parts.scheme or parts.netloc:
            return _scrub_url(value)
    return _scrub_query_string(value)


def _scrub_span_data(data: Any) -> Any:
    if isinstance(data, Mapping):
        return {
            name: _scrub_url_like(value) if _is_url_key(str(name)) else value for name, value in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [
            (name, _scrub_url_like(value) if _is_url_key(str(name)) else value) for name, value in data
        ]
    return data


def _scrub_spans(event: dict[str, Any]) -> None:
    spans = event.get("spans")
    if not isinstance(spans, list):
        return
    for span in spans:
        if isinstance(span, dict) and "data" in span:
            span["data"] = _scrub_span_data(span["data"])


def _looks_like_url_or_query(value: str) -> bool:
    if "=" in value and ("&" in value or "?" in value):
        return True
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return bool(parts.scheme and parts.netloc and parts.query)


def _scrub_recursive_entry(name: Any, value: Any) -> Any:
    text = str(name)
    if _is_secret_header(text) or _is_secret_query_param(text):
        return REDACTED
    if _is_url_key(text):
        value = _scrub_url_like(value)
    return _scrub_event_recursively(value)


def _scrub_event_recursively(value: Any) -> Any:
    """Scrub credentials anywhere in an event structure, whatever produced it."""
    if isinstance(value, dict):
        for name in list(value):
            value[name] = _scrub_recursive_entry(name, value[name])
        return value
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _scrub_event_recursively(item)
        return value
    if isinstance(value, tuple):
        return tuple(_scrub_event_recursively(item) for item in value)
    if isinstance(value, str) and _looks_like_url_or_query(value):
        return _scrub_url_like(value)
    return value


def _scrub_request(request: dict[str, Any]) -> None:
    if "query_string" in request:
        request["query_string"] = _scrub_query_string(request["query_string"])
    if "url" in request and isinstance(request["url"], str):
        request["url"] = _scrub_url(request["url"])
    if "headers" in request:
        request["headers"] = _scrub_headers(request["headers"])
    if "env" in request:
        request["env"] = _scrub_environ(request["env"])


def _scrub_event(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        return event
    request = event.get("request")
    if isinstance(request, dict):
        _scrub_request(request)
    _scrub_spans(event)
    # Last line of defence: Sentry adds the same request data to new keys in
    # each SDK release, so scrub the whole structure rather than named paths.
    return _scrub_event_recursively(event)


def before_send(event: Any, _hint: Any) -> Any:
    """Remove provider credentials from an error event before Sentry sends it."""
    return _scrub_event(event)


def before_send_transaction(event: Any, _hint: Any) -> Any:
    """Remove provider credentials from a transaction event before Sentry sends it."""
    return _scrub_event(event)


def init_sentry():
    """Initialize Sentry SDK if available and SENTRY_DSN is set."""
    global capture_exception
    if sentry_sdk is not None and os.getenv("SENTRY_DSN"):
        sentry_sdk.init(
            dsn=os.getenv("SENTRY_DSN"),
            # Request data is kept, but never PII or credentials collected by default.
            # See https://docs.sentry.io/platforms/python/data-management/data-collected/
            send_default_pii=False,
            before_send=before_send,
            before_send_transaction=before_send_transaction,
        )
        capture_exception = _capture_exception
