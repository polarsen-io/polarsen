import os
from pathlib import Path

_cur_dir = Path(__file__).parent
# Postgres

PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", 5432)
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "postgres")
PG_DATABASE = os.getenv("PG_DATABASE", "polarsen")

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
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "minioadmin")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "minioadmin")
S3_REGION = os.getenv("S3_REGION", "fr-par")
