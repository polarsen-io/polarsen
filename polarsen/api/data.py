from dataclasses import dataclass
from typing import TypedDict, NotRequired

import asyncpg
import bcrypt
from tracktolib.pg import insert_returning, insert_many

from polarsen.logs import logs


async def check_access_to_chat(conn: asyncpg.Connection, chat_id: int, user_id: int) -> bool:
    """
    Check if the user has access to the chat.
    """
    result = await conn.fetchval(
        """
        SELECT EXISTS (SELECT 1
                       FROM general.chat_members
                       WHERE chat_id = $1
                         AND user_id = $2)
        """,
        chat_id,
        user_id,
    )
    return result is True


def gen_pwd_hash(password: str, encoding: str = "utf-8") -> str:
    return bcrypt.hashpw(password.encode(encoding), bcrypt.gensalt(12)).decode(encoding)


class UserMeta(TypedDict):
    selected_model: NotRequired[str | None]
    selected_chat_id: NotRequired[int | None]


@dataclass
class User:
    @staticmethod
    async def upsert(
        conn: asyncpg.Connection,
        first_name: str | None = None,
        last_name: str | None = None,
        telegram_id: str | None = None,
        meta: UserMeta | None = None,
        api_keys: dict | None = None,
    ) -> int:
        """
        Create a new user and return the user ID.
        """
        if not conn.is_in_transaction():
            logs.warning("upsert called outside of a transaction, this may lead to unexpected behavior")

        if telegram_id is not None:
            internal_code = f"telegram_{telegram_id}"
        else:
            raise ValueError("Could not create user without telegram_id")
        data = {
            "first_name": first_name,
            "last_name": last_name,
            "telegram_id": telegram_id,
            "internal_code": internal_code,
            "meta": meta,
            "api_keys": api_keys,
        }
        user_id = await insert_returning(
            conn,
            "general.users",
            data,
            returning="id",
            on_conflict="""
                ON CONFLICT ON CONSTRAINT unique_users_idx
                DO UPDATE SET first_name = COALESCE(EXCLUDED.first_name, t.first_name),
                              last_name = COALESCE(EXCLUDED.last_name, t.last_name),
                              telegram_id = COALESCE(EXCLUDED.telegram_id, t.telegram_id),
                              internal_code = COALESCE(EXCLUDED.internal_code, t.internal_code),
                              meta = COALESCE(t.meta, '{}') || EXCLUDED.meta,
                              api_keys = COALESCE(t.api_keys, '{}') || EXCLUDED.api_keys
            """,
        )
        if user_id is None:
            user_id = await conn.fetchval("SELECT id FROM general.users WHERE telegram_id = $1", telegram_id)

        if user_id is None:
            raise ValueError(f"Failed to create or find user with telegram_id {telegram_id}")

        # Map the user to their telegram chats if telegram_id is provided
        if telegram_id:
            available_chats = await conn.fetch(
                """
                SELECT chat_id, id as chat_user_id
                from general.chat_users
                WHERE internal_code = $1
                """,
                telegram_id,
            )
            if not available_chats:
                logs.warning(f"No chats found for user with telegram_id {telegram_id!r}")
            else:
                data = [
                    {
                        "user_id": user_id,
                        "chat_id": chat["chat_id"],
                        "chat_user_id": chat["chat_user_id"],
                    }
                    for chat in available_chats
                ]
                await insert_many(conn, "general.user_chats", data, on_conflict="ON CONFLICT DO NOTHING")

        return user_id

    @staticmethod
    async def get_telegram_chat_username(conn: asyncpg.Connection, chat_id: int, user_id: int) -> str:
        """
        Get the username of the user in the chat.
        """
        username = await conn.fetchval(
            """
            SELECT username
            FROM general.chat_users
            WHERE chat_id = $1
              AND internal_code = (select telegram_id
                                   from general.users
                                   where id = $2)
            """,
            chat_id,
            user_id,
        )
        if username is None:
            raise ValueError(f"No chat user found for chat_id {chat_id!r} and user_id {user_id!r}")
        return username


@dataclass
class Question:
    question: str
    user_id: int
    meta: dict | None
    feedback: str | None = None

    @property
    def data(self) -> dict:
        return {
            "question": self.question,
            "meta": self.meta,
            "feedback": self.feedback,
            "user_id": self.user_id,
        }

    async def save(self, conn: asyncpg.Connection) -> int:
        question_id = await insert_returning(conn, "general.user_questions", self.data, returning="id")
        if question_id is None:
            raise ValueError(f"Failed to save question {self.question!r} for user {self.user_id}")
        return question_id

    @staticmethod
    async def update_feedback(conn: asyncpg.Connection, question_id: int, feedback: str) -> None:
        """
        Save feedback for a question.
        """
        await conn.execute(
            "UPDATE general.user_questions SET feedback = $1 WHERE id = $2",
            feedback,
            question_id,
        )
