import os
from pathlib import Path
from urllib.parse import quote

_cur_dir = Path(__file__).parent


def get_pg_password() -> str:
    """Return the Postgres password, or raise if it is not configured.

    Importing this module must not require the password: tests, the CLI and the
    listeners import it before they know which settings they need.
    """
    password = os.getenv("PG_PASSWORD")
    if password:
        return password
    raise RuntimeError("PG_PASSWORD environment variable must be set")


def get_pg_dsn() -> str:
    """Return the Postgres DSN, falling back to the individual PG_* variables."""
    dsn = os.getenv("PG_DSN")
    if dsn:
        return dsn
    host = os.getenv("PG_HOST", PG_HOST)
    port = os.getenv("PG_PORT", str(PG_PORT))
    user = quote(os.getenv("PG_USER", PG_USER), safe="")
    password = quote(get_pg_password(), safe="")
    database = os.getenv("PG_DATABASE", PG_DATABASE)
    return f"postgres://{user}:{password}@{host}:{port}/{database}"


def get_s3_access_key_id() -> str:
    """Return the S3 access key ID, or raise if it is not configured."""
    access_key_id = os.getenv("S3_ACCESS_KEY_ID")
    if access_key_id:
        return access_key_id
    raise RuntimeError("S3_ACCESS_KEY_ID environment variable must be set")


def get_s3_secret_access_key() -> str:
    """Return the S3 secret access key, or raise if it is not configured."""
    secret_access_key = os.getenv("S3_SECRET_ACCESS_KEY")
    if secret_access_key:
        return secret_access_key
    raise RuntimeError("S3_SECRET_ACCESS_KEY environment variable must be set")


# Postgres

PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", 5432)
PG_USER = os.getenv("PG_USER", "postgres")
PG_DATABASE = os.getenv("PG_DATABASE", "polarsen")
PG_DSN = os.getenv("PG_DSN")

SQL_DIR = Path(os.getenv("SQL_DIR", _cur_dir.parent / "sql"))

# Mistral
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# OpenAI
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
# Grok
GROK_API_KEY = os.environ.get("GROK_API_KEY")
# Self-Hosted OpenAI
SCALEWAY_API_KEY = os.environ.get("SCALEWAY_API_KEY")

# S3

CHAT_UPLOADS_S3_BUCKET = os.getenv("CHAT_UPLOADS_S3_BUCKET")

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://localhost:9000")
S3_REGION = os.getenv("S3_REGION", "fr-par")
