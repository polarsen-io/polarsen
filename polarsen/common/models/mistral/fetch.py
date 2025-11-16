from __future__ import annotations

from typing import TYPE_CHECKING, Any

import niquests

if TYPE_CHECKING:
    from mistralai.models import (
        AgentsCompletionRequestTypedDict,
        EmbeddingRequestTypedDict,
        ChatCompletionRequestTypedDict,
    )
else:
    EmbeddingRequestTypedDict = dict
    ChatCompletionRequestTypedDict = dict
    AgentsCompletionRequestTypedDict = dict

from http import HTTPStatus
from polarsen.db import UsageToken
from polarsen.env import MISTRAL_API_KEY
from ..utils import TooManyRequestsError

__all__ = (
    "fetch_completion",
    "fetch_embeddings",
    "UsageToken",
    "set_headers",
    "fetch_agent_completion",
)


def set_headers(session: niquests.Session, api_key: str | None = None) -> None:
    _api_key = api_key or MISTRAL_API_KEY
    if _api_key is None:
        raise ValueError("MISTRAL_API_KEY is not set")
    session.headers["Authorization"] = f"Bearer {_api_key}"


def _check_resp(resp: niquests.Response) -> dict:
    try:
        resp.raise_for_status()
    except niquests.exceptions.HTTPError as e:
        if resp.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            raise TooManyRequestsError(retry_delay=-1, response=resp) from e
        raise e
    return resp.json()


async def fetch_completion(
    session: niquests.AsyncSession,
    request: ChatCompletionRequestTypedDict,
) -> tuple[Any, UsageToken, ChatCompletionRequestTypedDict]:
    response = await session.post(
        "https://api.mistral.ai/v1/chat/completions",
        json=request,
    )
    data = _check_resp(response)

    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = [x for x in content if x["type"] == "text"][0]["text"]
        # thinking_content = [x for x in content if x['type'] == 'thinking'][0]['thinking']
    usage_token: UsageToken = {
        "total": data["usage"]["total_tokens"],
        "input": data["usage"]["prompt_tokens"],
        "output": data["usage"]["completion_tokens"],
    }
    return content, usage_token, request


async def fetch_embeddings(
    session: niquests.AsyncSession,
    inputs: str | list[str],
    model_name: str = "mistral-embed",
) -> tuple[list[float], UsageToken]:
    request = EmbeddingRequestTypedDict(
        model=model_name,
        input=inputs,  # pyright: ignore[reportCallIssue]
    )

    response = await session.post(
        "https://api.mistral.ai/v1/embeddings",
        json=request,
    )
    data = _check_resp(response)

    usage_token: UsageToken = {
        "total": data["usage"]["total_tokens"],
        "input": data["usage"]["prompt_tokens"],
        "output": data["usage"]["completion_tokens"],
    }
    return data["data"][0]["embedding"], usage_token


# def get_request_size(tokenizer: "MistralTokenizer", request: ChatCompletionRequest) -> int:
#     """
#     Get the number of tokens in the request
#     """
#     output = tokenizer.encode_chat_completion(request)
#     return len(output.tokens)


async def fetch_agent_completion(
    session: niquests.AsyncSession,
    request: AgentsCompletionRequestTypedDict,
) -> tuple[str, UsageToken, AgentsCompletionRequestTypedDict]:
    response = await session.post(
        "https://api.mistral.ai/v1/agents/completions",
        json=request,
    )

    data = _check_resp(response)
    content = data["choices"][0]["message"]["content"]
    usage_token: UsageToken = {
        "total": data["usage"]["total_tokens"],
        "input": data["usage"]["prompt_tokens"],
        "output": data["usage"]["completion_tokens"],
    }
    return content, usage_token, request
