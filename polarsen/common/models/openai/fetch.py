from __future__ import annotations
import pprint
from typing import overload, Literal, TYPE_CHECKING

import niquests

if TYPE_CHECKING:
    from openai.types import ChatModel
    from openai.types.chat import ChatCompletionMessageParam

from polarsen.env import OPENAI_API_KEY
from polarsen.db import UsageToken

__all__ = ("set_headers", "fetch_chat_completion")


def set_headers(session: niquests.Session):
    if OPENAI_API_KEY is None:
        raise ValueError("OPENAI_API_KEY is not set")
    session.headers["Authorization"] = f"Bearer {OPENAI_API_KEY}"


@overload
async def fetch_chat_completion(
    session: niquests.AsyncSession,
    model: ChatModel,
    messages: list[ChatCompletionMessageParam],
    temperature: float | None = None,
    seed: int | None = None,
    *,
    endpoint: Literal["https://api.openai.com/v1"],
) -> tuple[str, UsageToken, dict]: ...


@overload
async def fetch_chat_completion(
    session: niquests.AsyncSession,
    model: str,
    messages: list[ChatCompletionMessageParam],
    temperature: float | None = None,
    seed: int | None = None,
    *,
    endpoint: str,
) -> tuple[str, UsageToken, dict]: ...


async def fetch_chat_completion(
    session: niquests.AsyncSession,
    model: str,
    messages: list[ChatCompletionMessageParam],
    temperature: float | None = None,
    seed: int | None = None,
    *,
    endpoint: str = "https://api.openai.com/v1",
) -> tuple[str, UsageToken, dict]:
    """
    Fetch chat completion from OpenAI API using ChatCompletionCreateParams
    """
    payload: dict = {
        "model": model,
        "messages": messages,
    }

    if model in ("gpt-5-mini", "gpt-5-nano"):
        temperature = 1
    if temperature is not None:
        payload["temperature"] = temperature
    if seed is not None:
        payload["seed"] = seed

    response = await session.post(
        f"{endpoint}/chat/completions",
        json=payload,
    )

    try:
        response.raise_for_status()
    except niquests.exceptions.HTTPError as e:
        print(response.headers)
        pprint.pprint(response.json())
        raise e

    data = response.json()
    content = data["choices"][0]["message"]["content"]

    usage = data["usage"]
    usage_token: UsageToken = {
        "total": usage["total_tokens"],
        "input": usage["prompt_tokens"],
        "output": usage["completion_tokens"],
    }

    return content, usage_token, payload
