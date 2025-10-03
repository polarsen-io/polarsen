from __future__ import annotations

import http.client
import pprint
from typing import TYPE_CHECKING, Any

import niquests

if TYPE_CHECKING:
    from mistralai.models import AgentsCompletionRequestTypedDict
    from mistralai.models import EmbeddingRequestTypedDict, ChatCompletionRequestTypedDict

from polarsen.db import UsageToken
from polarsen.env import MISTRAL_API_KEY
from ..utils import TooManyRequestsError

__all__ = (
    "fetch_completion",
    "fetch_embeddings",
    "UsageToken",
    "set_headers",
    # "get_request_size",
    "fetch_agent_completion",
)


def set_headers(session: niquests.Session):
    if MISTRAL_API_KEY is None:
        raise ValueError("MISTRAL_API_KEY is not set")
    session.headers["Authorization"] = f"Bearer {MISTRAL_API_KEY}"


async def fetch_completion(
    session: niquests.AsyncSession,
    request: ChatCompletionRequestTypedDict,
) -> tuple[Any, UsageToken, ChatCompletionRequestTypedDict]:
    response = await session.post(
        "https://api.mistral.ai/v1/chat/completions",
        json=request,
    )
    try:
        response.raise_for_status()
    except niquests.exceptions.HTTPError as e:
        pprint.pprint(response.json())
        raise e

    data = response.json()
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
        inputs=inputs,
    )

    response = await session.post(
        "https://api.mistral.ai/v1/embeddings",
        json=request,
    )

    if response.status_code == http.client.TOO_MANY_REQUESTS:
        raise TooManyRequestsError(
            "Mistral API returned 429 Too Many Requests. Please try again later.", response=response
        )
    try:
        response.raise_for_status()
    except niquests.exceptions.HTTPError as e:
        print(response.text)
        raise e

    data = response.json()
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
    try:
        response.raise_for_status()
    except niquests.exceptions.HTTPError as e:
        pprint.pprint(response.json())
        raise e

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    usage_token: UsageToken = {
        "total": data["usage"]["total_tokens"],
        "input": data["usage"]["prompt_tokens"],
        "output": data["usage"]["completion_tokens"],
    }
    return content, usage_token, request
