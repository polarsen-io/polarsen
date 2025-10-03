import textwrap
from enum import Enum
from typing import Any

import asyncpg
from pydantic import TypeAdapter

from .models import Chat, User

__all__ = (
    "coerce_str_to_int",
    "APIException",
    "ErrorCode",
    "get_user",
    "get_user_chats",
    "check_32_bit",
)


def check_32_bit(n):
    return n < 1 << 31


def coerce_str_to_int(data: Any) -> int:
    if data is None:
        return -1
    try:
        value = int(data)
    except ValueError:
        return -1

    if not check_32_bit(value):
        return -1

    # To avoid "00001" being accepted
    if str(value) != str(data):
        value = -1

    return value


class ErrorCode(Enum):
    invalid_value = "invalid_value"
    invalid_body = "invalid_body"
    nothing_to_do = "nothing_to_do"
    already_exists = "already_exists"
    invalid_action = "invalid_action"
    invalid_headers = "invalid_headers"
    # Auth
    invalid_login = "invalid_login"
    invalid_token = "invalid_token"
    user_not_found = "user_not_found"
    email_taken = "email_taken"
    username_taken = "username_taken"
    # Files
    invalid_file = "invalid_file"
    not_found = "not_found"
    #
    forbidden = "forbidden"
    #
    timeout = "timeout"
    payment_error = "payment_error"
    # Email
    email_error = "email_error"


class APIException(Exception):
    def __init__(self, *, reason: str, error_code: ErrorCode, status_code: int = 422, **kwargs):
        self.status_code = status_code
        self.reason = reason
        self.error_code = error_code.value
        self.headers = kwargs.pop("headers", None)
        self.payload = kwargs


_ChatTypeAdapter = TypeAdapter(Chat)
_UserTypeAdapter = TypeAdapter(User)

_GET_USER_CHATS_QUERY = textwrap.dedent(
    """
    SELECT id, name, _last_msg.last_sent_at as cutoff_date
    FROM general.chats
             left join lateral (
        select max(sent_at)::date as last_sent_at
        from general.chat_messages
        where chat_id = general.chats.id
        ) as _last_msg on true
    where id in (select chat_id
                 from general.user_chats
                 where user_id = $1)
    ORDER BY name desc
    """
)


async def get_user_chats(conn: asyncpg.Connection, user_id: int) -> list[Chat]:
    chats = await conn.fetch(_GET_USER_CHATS_QUERY, user_id)
    return [_ChatTypeAdapter.validate_python(dict(chat)) for chat in chats]


_GET_USER_QUERY = """
                  select id,
                         first_name,
                         last_name,
                         telegram_id,
                         api_keys,
                         meta
                  from general.users
                  where ($1::int is null or id = $1)
                     OR ($2::text is not null and telegram_id = $2)
                  """


async def get_user(conn: asyncpg.Connection, user_id: int | None = None, telegram_id: str | None = None) -> User | None:
    """Load user data by user ID."""
    if user_id is None and telegram_id is None:
        raise ValueError("Either user_id or telegram_id must be provided")
    user = await conn.fetchrow(
        _GET_USER_QUERY,
        user_id,
        telegram_id,
    )
    if user is None:
        return None

    chats = await get_user_chats(conn, user_id=user["id"])
    return _UserTypeAdapter.validate_python(
        {
            **dict(user),
            "chats": chats,
        }
    )
