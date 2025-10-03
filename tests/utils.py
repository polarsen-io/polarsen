import psycopg
from tracktolib.pg_sync import get_tables, clean_tables


def generate_large_file(size_bytes: int = 5 * 1024 * 1024) -> bytes:
    """Generate a large file of specified size in bytes."""

    pattern = b"This is test data for large file upload testing. " * 100
    pattern_size = len(pattern)

    # Calculate how many full patterns we need
    full_patterns = size_bytes // pattern_size
    remainder = size_bytes % pattern_size

    # Build the large file content
    large_content = pattern * full_patterns + pattern[:remainder]
    return large_content


_CLEAN_IGNORE_TABLES = {"general.chat_types"}
_TABLES = None
SCHEMAS = ["ai", "general"]


def clean_pg_fn(engine: psycopg.Connection):
    global _TABLES
    if _TABLES is None:
        _TABLES = get_tables(engine, schemas=SCHEMAS, ignored_tables=_CLEAN_IGNORE_TABLES)
    clean_tables(engine, _TABLES)
