from __future__ import annotations

from typing import TYPE_CHECKING

import niquests

if TYPE_CHECKING:
    from google.genai import types

from polarsen.env import GEMINI_API_KEY
from polarsen.logs import logs
from polarsen.db import UsageToken

__all__ = ("fetch_completion",)


def _get_api_key() -> str:
    if GEMINI_API_KEY is None:
        raise ValueError("GEMINI_API_KEY is not set")
    return GEMINI_API_KEY


async def fetch_completion(
    session: niquests.AsyncSession,
    model: str,
    contents: list[types.Content],
    config: types.GenerateContentConfig,
    api_key: str | None = None,
) -> tuple[str, UsageToken, dict]:
    if config.candidate_count is None:
        config.candidate_count = 1
    if config.candidate_count > 1:
        logs.warning("Candidate count > 1 is not supported, using 1")
        config.candidate_count = 1

    _config = config.model_dump(exclude_none=True)
    _sys_instruction = _config.pop("system_instruction", None)
    payload = {"generation_config": _config, "contents": [x.model_dump(exclude_none=True) for x in contents]}
    if _sys_instruction is not None:
        payload["system_instruction"] = _sys_instruction

    if api_key is None:
        api_key = _get_api_key()

    response = await session.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
        json=payload,
    )
    try:
        response.raise_for_status()
    except niquests.exceptions.HTTPError as e:
        # print(response.headers)
        print(response.text)
        raise e

    data = response.json()
    candidate = data["candidates"][0]
    usage = data["usageMetadata"]
    token: UsageToken = {
        "total": usage["totalTokenCount"],
        "input": usage["promptTokenCount"],
        "output": usage["candidatesTokenCount"],
        "cached": usage.get("cachedContentTokenCount", 0),
    }
    content = candidate["content"]["parts"][0]["text"]
    return content, token, payload
