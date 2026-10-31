import os

from polarsen.env import get_internal_api_token

TELEGRAM_BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_URI = os.getenv("API_URI", "http://localhost:5050")


def api_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return headers for internal API requests, including the shared bearer token."""
    headers = {"Authorization": f"Bearer {get_internal_api_token()}"}
    if extra:
        headers.update(extra)
    return headers
