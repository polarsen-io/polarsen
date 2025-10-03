from __future__ import annotations

import contextlib
import datetime as dt
import json
import textwrap
from dataclasses import dataclass, field
from typing import Sequence, TYPE_CHECKING

import asyncpg
import niquests

if TYPE_CHECKING:
    import mistralai.models as mistral_models
    from google.genai import types as genai_types
    from openai.types import chat as openai_types

from polarsen.db import UsageToken
from polarsen.logs import logs
from .models import mistral, gemini, openai, grok, self_hosted
from .models.gemini import is_thinking_only_model
from .search import search_close_messages, CloseEmbedding
from .utils import AISource

ASK_SYSTEM_PROMPT = textwrap.dedent("""
You are a helpful assistant that answers questions based on the provided context.
If the question is not related to the context, say "I don't know".
The context summary is provided in the [CONTEXT][/CONTEXT] tags.
The question is provided in the [QUESTION][/QUESTION] tags.
Answer using the same language than the question.
You can use the timestamp of the context to provide a more accurate answer.
The name of the user asking the question is provided in the [USER][/USER] tags.
The current date of the question is provided in the [DATE][/DATE] tags.
""")


def fmt_summaries(results: list[CloseEmbedding]) -> list[dict]:
    return [
        {
            "summary": x["summary"],
            "day": x["day"].isoformat(),
            "messages": x["messages"],
        }
        for x in results
    ]


def fmt_context_message(context: list[dict], user: str) -> str:
    _cur_date = dt.datetime.now().date().isoformat()
    message = f"""
    [CONTEXT]{json.dumps(context)}[/CONTEXT]
    [DATE]{dt.datetime.now().date().isoformat()}[/DATE]
    [USER]{user}[/USER]
    """
    return textwrap.dedent(message).strip()


@dataclass
class ChatSession:
    model_name: str
    """Key for the RAG model"""
    rag_api_key: str
    api_key: str
    _input_token_count: int = field(default=0, init=False)
    _output_token_count: int = field(default=0, init=False)
    _cached_token_count: int = field(default=0, init=False)

    @property
    def intput_token_count(self):
        return self._input_token_count

    @property
    def output_token_count(self):
        return self._output_token_count

    @property
    def cached_token_count(self):
        return self._cached_token_count

    @classmethod
    def get_session(cls, model_name: str, rag_api_key: str, api_key: str):
        if mistral.is_mistral_model(model_name):
            return MistralChatSession(model_name=model_name, api_key=api_key, rag_api_key=rag_api_key)
        elif gemini.is_gemini_model(model_name):
            return GeminiChatSession(model_name=model_name, api_key=api_key, rag_api_key=rag_api_key)
        elif openai.is_openai_model(model_name) or grok.is_grok_model(model_name):
            return OpenAIChatSession(model_name=model_name, api_key=api_key, rag_api_key=rag_api_key)
        else:
            raise ValueError(f"Model {model_name!r} is not supported")

    async def fetch_close_messages(
        self,
        conn: asyncpg.Connection,
        session: niquests.AsyncSession,
        chat_id: int,
        question: str,
        limit: int = 5,
    ):
        with set_auth_headers(session, self.rag_api_key):
            search_results = await search_close_messages(session, conn, question=question, chat_id=chat_id, limit=limit)
        return search_results

    def set_token(self, token: UsageToken):
        self._cached_token_count += token.get("cached", 0)
        self._input_token_count += token["input"]
        self._output_token_count += token["output"]

    def clear(self):
        self._cached_token_count = 0
        self._input_token_count = 0
        self._output_token_count = 0


@dataclass
class MistralChatSession(ChatSession):
    model_name: str
    messages: list[mistral_models.MessagesTypedDict] = field(default_factory=list)

    def __post_init__(self):
        if not mistral.is_mistral_model(self.model_name):
            raise ValueError(f"Model {self.model_name} is not a Mistral model")

    @property
    def model_source(self) -> AISource:
        return "mistral"

    async def ask_rag(
        self,
        conn: asyncpg.Connection,
        session: niquests.AsyncSession,
        chat_id: int,
        question: str,
        user: str,
        limit: int = 5,
    ):
        search_results = await self.fetch_close_messages(
            conn=conn,
            session=session,
            chat_id=chat_id,
            question=question,
            limit=limit,
        )
        summaries = fmt_summaries(search_results)
        messages = [
            mistral_models.SystemMessage(content=ASK_SYSTEM_PROMPT),
            mistral_models.AssistantMessage(content=fmt_context_message(summaries, user)),
            mistral_models.UserMessage(content=f"[QUESTION]{question}[/QUESTION]"),
        ]
        response = await self.ask(session=session, messages=messages)
        debug_data = {"results": search_results}
        return response, debug_data

    async def ask(self, session: niquests.AsyncSession, messages: Sequence[mistral_models.MessagesTypedDict]):
        self.messages += messages
        request = mistral_models.ChatCompletionRequestTypedDict(messages=self.messages, model=self.model_name)
        resp, token, _ = await mistral.fetch_completion(session, request)
        self.messages.append(mistral_models.AssistantMessageTypedDict(content=resp))
        self.set_token(token)
        return resp

    def clear(self):
        super().clear()
        self.messages = []


@dataclass
class GeminiChatSession(ChatSession):
    model_name: str
    messages: list[genai_types.Content] = field(default_factory=list)

    def __post_init__(self):
        if not gemini.is_gemini_model(self.model_name):
            raise ValueError(f"Model {self.model_name} is not a Gemini model")

    @property
    def model_source(self) -> AISource:
        return "gemini"

    async def ask_rag(
        self,
        conn: asyncpg.Connection,
        session: niquests.AsyncSession,
        chat_id: int,
        question: str,
        user: str,
        limit: int = 5,
    ):
        search_results = await self.fetch_close_messages(
            conn=conn,
            session=session,
            chat_id=chat_id,
            question=question,
            limit=limit,
        )
        summaries = fmt_summaries(search_results)

        messages = [
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_text(text=ASK_SYSTEM_PROMPT),
                ],
            ),
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_text(text=fmt_context_message(summaries, user)),
                ],
            ),
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_text(text=f"[QUESTION]{question}[/QUESTION]"),
                ],
            ),
        ]
        if "thinking" in self.model_name:
            self.model_name = self.model_name.replace(" (thinking)", "")
            disable_thinking = False
        elif is_thinking_only_model(self.model_name):
            disable_thinking = False
        else:
            disable_thinking = True

        response = await self.ask(session=session, messages=messages, disable_thinking=disable_thinking)
        debug_data = {"results": search_results}
        return response, debug_data

    async def ask(
        self,
        session: niquests.AsyncSession,
        messages: list[genai_types.Content],
        temperature: float | None = None,
        seed: int | None = None,
        disable_thinking: bool = True,
    ):
        if is_thinking_only_model(self.model_name) and disable_thinking:
            # If the model is a thinking-only model, we disable thinking
            disable_thinking = False
            logs.warning(f"Model {self.model_name!r} is a thinking-only model, enabling thinking.")
        self.messages += messages
        config = genai_types.GenerateContentConfig(
            temperature=temperature,
            seed=seed,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0) if disable_thinking else None,
        )
        session.headers.pop("Authorization", None)  # Remove any existing Authorization header
        resp, token, payload = await gemini.fetch_completion(
            session, model=self.model_name, config=config, contents=messages, api_key=self.api_key
        )
        self.messages.append(
            genai_types.Content(
                role="model",
                parts=[
                    genai_types.Part.from_text(text=resp),
                ],
            )
        )
        self.set_token(token)
        return resp

    def clear(self):
        super().clear()
        self.messages = []


ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "grok": "https://api.x.ai/v1",
}


@dataclass
class OpenAIChatSession(ChatSession):
    model_name: str
    messages: list[openai_types.ChatCompletionMessageParam] = field(default_factory=list)
    endpoint: str | None = None

    def __post_init__(self):
        if self.endpoint is None:
            _endpoint = ENDPOINTS.get(self.model_source)
            if not _endpoint:
                raise ValueError(f"Model {self.model_name!r} does not have a valid endpoint")
            self.endpoint = _endpoint

    @property
    def model_source(self) -> AISource:
        _source: AISource | None = None
        if openai.is_openai_model(self.model_name):
            _source = "openai"
        elif grok.is_grok_model(self.model_name):
            _source = "grok"
        elif self_hosted.is_self_hosted_model(self.model_name):
            _source = "self_hosted"

        if _source is None:
            raise ValueError(f"Model {self.model_name!r} is not supported")

        return _source

    async def ask_rag(
        self,
        conn: asyncpg.Connection,
        session: niquests.AsyncSession,
        chat_id: int,
        question: str,
        user: str,
        limit: int = 5,
    ):
        search_results = await self.fetch_close_messages(
            conn=conn,
            session=session,
            chat_id=chat_id,
            question=question,
            limit=limit,
        )
        summaries = fmt_summaries(search_results)
        messages: list[openai_types.ChatCompletionMessageParam] = [
            openai_types.ChatCompletionSystemMessageParam(
                content=ASK_SYSTEM_PROMPT,
                role="system",
            ),
            openai_types.ChatCompletionAssistantMessageParam(
                content=fmt_context_message(summaries, user),
                role="assistant",
            ),
            openai_types.ChatCompletionUserMessageParam(
                role="user",
                content=f"[QUESTION]{question}[/QUESTION]",
            ),
        ]
        response = await self.ask(session=session, messages=messages)
        debug_data = {"results": search_results}
        return response, debug_data

    async def ask(self, session: niquests.AsyncSession, messages: list[openai_types.ChatCompletionMessageParam]):
        self.messages += messages
        if self.endpoint is None:
            raise ValueError("Endpoint is not set for OpenAIChatSession")
        resp, token, _ = await openai.fetch_chat_completion(
            session, model=self.model_name, messages=messages, endpoint=self.endpoint
        )
        self.messages.append(
            openai_types.ChatCompletionAssistantMessageParam(
                role="assistant",
                content=resp,
            )
        )
        self.set_token(token)
        return resp

    def clear(self):
        super().clear()
        self.messages = []


@contextlib.contextmanager
def set_auth_headers(session: niquests.AsyncSession, api_key: str):
    auth = session.headers.get("Authorization")
    if api_key is None:
        raise ValueError("API key is not set")
    session.headers["Authorization"] = f"Bearer {api_key}"
    try:
        yield
    finally:
        if auth is None:
            session.headers.pop("Authorization", None)
        else:
            session.headers["Authorization"] = auth
