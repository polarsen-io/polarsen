from typing import TYPE_CHECKING

import niquests

if TYPE_CHECKING:
    from openai.types import ChatModel
    from openai.types.chat import ChatCompletionMessageParam

from polarsen.db import UsageToken
from polarsen.env import GROK_API_KEY

from .. import openai

__all__ = ("fetch_chat_completion", "set_headers")


HEADER_KEY = "Authorization"


def set_headers(session: niquests.Session, api_key: str | None = None) -> str:
    _api_key = api_key or GROK_API_KEY
    if _api_key is None:
        raise ValueError("GROK_API_KEY is not set")
    session.headers[HEADER_KEY] = f"Bearer {_api_key}"
    return HEADER_KEY


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
