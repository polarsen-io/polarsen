import os

try:
    import sentry_sdk
    from sentry_sdk import capture_exception
except ImportError:
    sentry_sdk = None
    capture_exception = None

if sentry_sdk is not None and os.getenv("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN"),
        # Add data like request headers and IP for users,
        # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
        send_default_pii=True,
    )
else:
    capture_exception = None
