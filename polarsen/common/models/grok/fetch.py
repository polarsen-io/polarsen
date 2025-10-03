from __future__ import annotations
import niquests
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai.types import ChatModel
    from openai.types.chat import ChatCompletionMessageParam

from polarsen.env import GROK_API_KEY
from .. import openai
from polarsen.db import UsageToken

__all__ = ("set_headers", "fetch_chat_completion")


def set_headers(session: niquests.Session):
    if GROK_API_KEY is None:
        raise ValueError("GROK_API_KEY is not set")
    session.headers["Authorization"] = f"Bearer {GROK_API_KEY}"


async def fetch_chat_completion(
    session: niquests.AsyncSession,
    model: ChatModel,
    messages: list[ChatCompletionMessageParam],
    temperature: float | None = None,
    seed: int | None = None,
) -> tuple[str, UsageToken, dict]:
    return await openai.fetch_chat_completion(
        session=session,
        model=model,
        messages=messages,
        temperature=temperature,
        seed=seed,
        endpoint="https://api.x.ai/v1",
    )
