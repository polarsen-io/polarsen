from __future__ import annotations

import datetime as dt
from typing import TypedDict, TYPE_CHECKING

import niquests

if TYPE_CHECKING:
    from mistralai import ChatCompletionRequestTypedDict
    from mistralai.models import AssistantMessage, UserMessage

from .fetch import fetch_completion, UsageToken

__all__ = ("get_request", "get_request_messages", "get_request_messages", "get_messages_completion")

SUMMARY_PROMPT = "I want you to make a short summary (in {lang}) of this messages."


class Message(TypedDict):
    sent_at: dt.date | str
    username: str
    message: str


def fmt_discussion(messages: list[Message]) -> str:
    output = []
    for msg in messages:
        _sent_at = msg["sent_at"]
        output.append(f"[{_sent_at}] {msg['username']}: {msg['message']}")

    return "\n".join(output)


def get_request_messages(messages: list[Message], lang: str):
    match lang:
        case "french":
            prefix = "Réponse en français: "
        case "english":
            prefix = "Answer in english: "
        case _:
            raise ValueError(f"Got invalid lang {lang!r}")
    _messages = [
        AssistantMessage(content=SUMMARY_PROMPT.format(lang=lang)),
        UserMessage(content=fmt_discussion(messages)),
        AssistantMessage(content=prefix, prefix=True),
    ]
    return _messages, prefix


def get_request(
    messages: list[Message],
    lang: str,
    model_name: str = "nemostral",
) -> tuple[ChatCompletionRequestTypedDict, int]:
    _req_messages, prefix = get_request_messages(messages, lang)
    request = ChatCompletionRequestTypedDict(
        messages=_req_messages,
        model=model_name,
    )
    return request, len(prefix)


async def get_messages_completion(
    session: niquests.AsyncSession, messages: list[Message], lang: str, model_name: str
) -> tuple[str, UsageToken]:
    request, _prefix_size = get_request(messages, lang, model_name)
    content, token, _ = await fetch_completion(session, request)
    content = content[_prefix_size:].strip().strip('"')
    return content, token
