import os

try:
    import sentry_sdk
    from sentry_sdk import capture_exception as _capture_exception
except ImportError:
    sentry_sdk = None
    _capture_exception = None

capture_exception = None


def init_sentry():
    """Initialize Sentry SDK if available and SENTRY_DSN is set."""
    global capture_exception
    if sentry_sdk is not None and os.getenv("SENTRY_DSN"):
        sentry_sdk.init(
            dsn=os.getenv("SENTRY_DSN"),
            # Add data like request headers and IP for users,
            # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
            send_default_pii=True,
        )
        capture_exception = _capture_exception
