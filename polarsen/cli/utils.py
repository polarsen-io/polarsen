import contextlib
import os
import re

__all__ = ("set_pg_env",)

PGURL_REG = re.compile(
    r"postgres(ql)?://(?P<user>[^:]+):(?P<password>[^@]+)@(?P<host>[^:]+):(?P<port>[^/]+)/(?P<db>[^/]+)"
)


@contextlib.contextmanager
def set_pg_env(pg_url: str):
    keys = {
        "PGHOST": "host",
        "PGPORT": "port",
        "PGUSER": "user",
        "PGPASSWORD": "password",
        "PGDATABASE": "db",
    }
    if not (match := PGURL_REG.match(pg_url)):
        raise ValueError(f"Invalid PG URL: {pg_url!r}")
    _saved_keys: dict = {key: os.environ.get(key) for key in keys if os.environ.get(key)}
    groups = match.groupdict()
    for key, value in keys.items():
        os.environ[key] = groups[value]
    try:
        yield groups
    finally:
        for key in keys:
            os.environ.pop(key, None)
        os.environ.update(_saved_keys)
