from __future__ import annotations

from typing import TYPE_CHECKING

import niquests

if TYPE_CHECKING:
    from google.genai import types
import math
from polarsen.env import GEMINI_API_KEY
from polarsen.logs import logs
from polarsen.db import UsageToken
from ..utils import TooManyRequestsError, QuotaExceededError, retry_async
from http import HTTPStatus

__all__ = ("fetch_completion", "set_headers")

HEADER_KEY = "x-goog-api-key"


def set_headers(session: niquests.Session, api_key: str | None = None) -> str:
    _api_key = api_key or GEMINI_API_KEY
    if _api_key is None:
        raise ValueError("GEMINI_API_KEY is not set")
    session.headers[HEADER_KEY] = _api_key
    return HEADER_KEY


def _parse_retry_delay(delay_str: str) -> int:
    """Parse retry delay string like '48s' or '48.24916365s' to int seconds (rounded up)."""

    return math.ceil(float(delay_str.rstrip("s")))


def _check_resp(resp: niquests.Response) -> dict:
    try:
        resp.raise_for_status()
    except niquests.exceptions.HTTPError as e:
        data = resp.json()
        if resp.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            _retry_delay: int | None = None
            for _detail in data["error"]["details"]:
                if _detail["@type"] == "type.googleapis.com/google.rpc.QuotaFailure":
                    raise QuotaExceededError(body=data)
                if _detail["@type"] == "type.googleapis.com/google.rpc.RetryInfo":
                    _retry_delay = _parse_retry_delay(_detail["retryDelay"])
            if _retry_delay is None:
                raise ValueError("Too many requests but no retry info found") from e
            raise TooManyRequestsError(retry_delay=_retry_delay)
        raise e
    return resp.json()


@retry_async()
async def fetch_completion(
    session: niquests.AsyncSession,
    model: str,
    contents: list[types.Content],
    config: types.GenerateContentConfig,
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

    response = await session.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        json=payload,
    )
    data = _check_resp(response)
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
