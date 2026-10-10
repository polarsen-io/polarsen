import os

try:
    import sentry_sdk
    from sentry_sdk import capture_exception as _capture_exception
except ImportError:
    sentry_sdk = None
    _capture_exception = None

capture_exception = None


def set_user(user_id: int | str | None) -> None:
    """Attach a minimal user identifier to the current Sentry scope (id only, no PII)."""
    if sentry_sdk is None or user_id is None:
        return
    sentry_sdk.set_user({"id": str(user_id)})


def init_sentry():
    """Initialize Sentry SDK if available and SENTRY_DSN is set."""
    global capture_exception
    if sentry_sdk is not None and os.getenv("SENTRY_DSN"):
        sentry_sdk.init(
            dsn=os.getenv("SENTRY_DSN"),
            environment=os.getenv("ENV", "production"),
            # Privacy: do not send IPs, request headers, cookies, or request bodies.
            # If user context is needed for an event, attach it explicitly with sentry_sdk.set_user.
            send_default_pii=False,
        )
        capture_exception = _capture_exception
