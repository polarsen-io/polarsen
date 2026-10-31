from __future__ import annotations

import niquests
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai.types import ChatModel
    from openai.types.chat import ChatCompletionMessageParam

from polarsen.env import SCALEWAY_API_KEY
from .. import openai
from polarsen.db import UsageToken
from .utils import SELF_HOSTED_ENDPOINTS

__all__ = ("set_headers", "fetch_chat_completion")


HEADER_KEY = "Authorization"


def set_headers(session: niquests.Session) -> str:
    if SCALEWAY_API_KEY is None:
        raise ValueError("SCALEWAY_API_KEY is not set")
    session.headers[HEADER_KEY] = f"Bearer {SCALEWAY_API_KEY}"
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
        endpoint=SELF_HOSTED_ENDPOINTS[model],
    )
