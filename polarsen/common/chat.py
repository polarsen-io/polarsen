from __future__ import annotations

import contextlib
import datetime as dt
import json
import textwrap
from dataclasses import dataclass, field
from typing import Sequence, TYPE_CHECKING, Literal

import asyncpg
import niquests

if TYPE_CHECKING:
    import mistralai.models as mistral_models
    from google.genai import types as genai_types
    from openai.types import chat as openai_types

from polarsen.db import UsageToken
from polarsen.logs import logs
from polarsen import env
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
    """Base class for chat sessions with different AI providers."""

    model_name: str
    api_keys: dict[AISource, str] = field(default_factory=dict)
    rag_model_name: Literal["mistral"] = "mistral"

    _input_token_count: int = field(default=0, init=False)
    _output_token_count: int = field(default=0, init=False)
    _cached_token_count: int = field(default=0, init=False)

    @property
    def model_source(self) -> AISource:
        raise NotImplementedError("model_source must be implemented in subclasses")

    @property
    def rag_model_source(self) -> AISource:
        return "mistral"

    @property
    def api_key(self) -> str:
        """Return the API key for the current model source."""
        source = self.model_source
        api_key = self.api_keys.get(source)
        if not api_key:
            raise ValueError(f"API key for source {source!r} is not set")
        return api_key

    @property
    def rag_api_key(self) -> str:
        """Return the RAG API key for fetching close messages."""
        api_key = self.api_keys.get(self.rag_model_name)
        if not api_key:
            logs.warning("No RAG API key set, using default MISTRAL_API_KEY from environment")
            return env.MISTRAL_API_KEY or ""
        return api_key

    @property
    def input_token_count(self) -> int:
        """Return the total number of input tokens used."""
        return self._input_token_count

    @property
    def output_token_count(self) -> int:
        """Return the total number of output tokens used."""
        return self._output_token_count

    @property
    def cached_token_count(self) -> int:
        """Return the total number of cached tokens used."""
        return self._cached_token_count

    @classmethod
    def get_session(cls, model_name: str, api_keys: dict[AISource, str] | None = None):
        """
        Factory method to create the appropriate chat session based on the model name.
        """
        _api_keys = api_keys or {}
        if mistral.is_mistral_model(model_name):
            return MistralChatSession(model_name=model_name, api_keys=_api_keys)
        elif gemini.is_gemini_model(model_name):
            return GeminiChatSession(model_name=model_name, api_keys=_api_keys)
        elif openai.is_openai_model(model_name) or grok.is_grok_model(model_name):
            return OpenAIChatSession(model_name=model_name, api_keys=_api_keys)
        else:
            raise ValueError(f"Model {model_name!r} is not supported")

    async def fetch_close_messages(
        self,
        conn: asyncpg.Connection,
        session: niquests.AsyncSession,
        chat_id: int,
        question: str,
        limit: int = 5,
    ) -> list[CloseEmbedding]:
        """
        Fetch message groups that are semantically close to the given question.
        """
        with set_auth_headers(session, self.rag_api_key, self.rag_model_source):
            search_results = await search_close_messages(session, conn, question=question, chat_id=chat_id, limit=limit)
        return search_results

    def set_token(self, token: UsageToken) -> None:
        """
        Update token counts from a usage token response.
        """
        self._cached_token_count += token.get("cached", 0)
        self._input_token_count += token["input"]
        self._output_token_count += token["output"]

    def clear(self) -> None:
        """Reset all token counts to zero."""
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
        import mistralai.models as mistral_models

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
        import mistralai.models as mistral_models

        self.messages += messages
        request = mistral_models.ChatCompletionRequestTypedDict(messages=self.messages, model=self.model_name)
        with set_auth_headers(session, self.api_key, self.model_source):
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
        from google.genai import types as genai_types

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
        from google.genai import types as genai_types

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
        with set_auth_headers(session, self.api_key, self.model_source):
            resp, token, payload = await gemini.fetch_completion(
                session, model=self.model_name, config=config, contents=messages
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
        from openai.types import chat as openai_types

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
        from openai.types import chat as openai_types

        self.messages += messages
        if self.endpoint is None:
            raise ValueError("Endpoint is not set for OpenAIChatSession")

        with set_auth_headers(session, self.api_key, self.model_source):
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


# Mapping of AI source to set_headers function
_SET_HEADERS_FUNCS = {
    "mistral": mistral.set_headers,
    "gemini": gemini.set_headers,
    "openai": openai.set_headers,
    "grok": grok.set_headers,
    "self_hosted": lambda session, api_key=None: self_hosted.set_headers(session),
}

# Header keys used by each AI source (for saving/restoring previous values)
_AUTH_HEADER_KEYS: dict[AISource, str] = {
    "mistral": "Authorization",
    "gemini": "x-goog-api-key",
    "openai": "Authorization",
    "grok": "Authorization",
    "self_hosted": "Authorization",
}


@contextlib.contextmanager
def set_auth_headers(session: niquests.AsyncSession, api_key: str, source: AISource):
    """
    Context manager to temporarily set authentication headers for a given AI source.

    Reuses the set_headers functions from each model module.
    """
    if api_key is None:
        raise ValueError("API key is not set")

    set_headers_func = _SET_HEADERS_FUNCS.get(source)
    if set_headers_func is None:
        raise ValueError(f"Unknown AI source: {source!r}")

    # Get the header key to save/restore previous value
    header_key = _AUTH_HEADER_KEYS[source]
    previous_value = session.headers.get(header_key)

    # Call set_headers to set the authentication header
    set_headers_func(session, api_key=api_key)

    try:
        yield
    finally:
        if previous_value is None:
            session.headers.pop(header_key, None)
        else:
            session.headers[header_key] = previous_value
