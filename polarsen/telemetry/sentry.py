import os
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from sentry_sdk.types import Breadcrumb, BreadcrumbHint, Event, Hint

try:
    import sentry_sdk
    from sentry_sdk import capture_exception as _capture_exception
except ImportError:
    sentry_sdk = None
    _capture_exception = None

capture_exception = None


def _strip_query(url: str) -> str:
    return url.split("?", 1)[0]


def scrub_event(event: "Event", _hint: "Hint") -> "Event":
    """Drop query strings and bodies: they carry API keys and user questions (e.g. /chats/{id}/ask)."""
    request = cast(dict[str, Any], event).get("request")
    if isinstance(request, dict):
        request.pop("query_string", None)
        request.pop("data", None)
        if isinstance(request.get("url"), str):
            request["url"] = _strip_query(request["url"])
    for crumb in (cast(dict[str, Any], event).get("breadcrumbs") or {}).get("values", []):
        scrub_breadcrumb(crumb, {})
    return event


def scrub_breadcrumb(crumb: "Breadcrumb", _hint: "BreadcrumbHint") -> "Breadcrumb":
    """Drop query strings from HTTP client breadcrumbs (the bot calls the API with api_key in the query)."""
    data = cast(dict[str, Any], crumb).get("data")
    if isinstance(data, dict):
        data.pop("http.query", None)
        if isinstance(data.get("url"), str):
            data["url"] = _strip_query(data["url"])
    return crumb


def init_sentry():
    """Initialize Sentry SDK if available and SENTRY_DSN is set."""
    global capture_exception
    if sentry_sdk is not None and os.getenv("SENTRY_DSN"):
        sentry_sdk.init(
            dsn=os.getenv("SENTRY_DSN"),
            # No user data: API keys and chat content live in locals, bodies and query strings
            send_default_pii=False,
            include_local_variables=False,
            max_request_body_size="never",
            before_send=scrub_event,
            before_breadcrumb=scrub_breadcrumb,
        )
        capture_exception = _capture_exception
