import typing
from http import HTTPStatus
from typing import NamedTuple
from unittest.mock import AsyncMock, MagicMock

import niquests
import pytest

if typing.TYPE_CHECKING:
    from mistralai.models import AgentsCompletionRequestTypedDict
else:
    AgentsCompletionRequestTypedDict = typing.Any

from polarsen.common.models.mistral.fetch import (
    fetch_completion,
    fetch_embeddings,
    fetch_agent_completion,
    set_headers,
    HEADER_KEY,
)
from polarsen.common.models.utils import TooManyRequestsError


class CompletionCase(NamedTuple):
    content_data: str | list
    expected_content: str


class EmbeddingsCase(NamedTuple):
    inputs: str | list[str]
    embeddings_data: list[dict]
    expected_embeddings: list[list[float]]


class ErrorCase(NamedTuple):
    status_code: int
    expected_exception: type[Exception]
    expected_retry_delay: int | None = None


class TestSetHeaders:
    def test_sets_bearer_token_from_param(self):
        session = MagicMock(spec=niquests.Session)
        session.headers = {}

        result = set_headers(session, api_key="test-key")

        assert result == HEADER_KEY
        assert session.headers[HEADER_KEY] == "Bearer test-key"

    def test_raises_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr("polarsen.common.models.mistral.fetch.MISTRAL_API_KEY", None)
        session = MagicMock(spec=niquests.Session)
        session.headers = {}

        with pytest.raises(ValueError, match="MISTRAL_API_KEY is not set"):
            set_headers(session, api_key=None)


class TestFetchCompletion:
    @pytest.fixture
    def mock_session(self):
        return AsyncMock(spec=niquests.AsyncSession)

    @staticmethod
    def _setup_response(session, response_data):
        resp = MagicMock()
        resp.json.return_value = response_data
        resp.raise_for_status = MagicMock()
        session.post.return_value = resp

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                CompletionCase(content_data="Hello, world!", expected_content="Hello, world!"),
                id="string_content",
            ),
            pytest.param(
                CompletionCase(
                    content_data=[{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "Answer"}],
                    expected_content="Answer",
                ),
                id="list_content_with_thinking",
            ),
        ],
    )
    def test_returns_content(self, loop, mock_session, case: CompletionCase):
        self._setup_response(
            mock_session,
            {
                "choices": [{"message": {"content": case.content_data}}],
                "usage": {"total_tokens": 100, "prompt_tokens": 50, "completion_tokens": 50},
            },
        )

        content, token, _ = loop.run_until_complete(
            fetch_completion(session=mock_session, request={"model": "mistral-large", "messages": []})  # type: ignore[arg-type]
        )

        assert content == case.expected_content
        assert token == {"total": 100, "input": 50, "output": 50}

    def test_calls_correct_endpoint(self, loop, mock_session):
        self._setup_response(
            mock_session,
            {
                "choices": [{"message": {"content": "Hi"}}],
                "usage": {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5},
            },
        )

        loop.run_until_complete(
            fetch_completion(session=mock_session, request={"model": "mistral-large", "messages": []})  # type: ignore[arg-type]
        )

        mock_session.post.assert_called_once()
        assert mock_session.post.call_args[0][0] == "https://api.mistral.ai/v1/chat/completions"

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                ErrorCase(
                    status_code=HTTPStatus.TOO_MANY_REQUESTS,
                    expected_exception=TooManyRequestsError,
                    expected_retry_delay=-1,
                ),
                id="too_many_requests",
            ),
        ],
    )
    def test_error_handling(self, monkeypatch, loop, mock_session, case: ErrorCase):
        monkeypatch.setattr("polarsen.common.models.utils.asyncio.sleep", AsyncMock())

        resp = MagicMock()
        resp.status_code = case.status_code
        resp.headers = {}
        resp.raise_for_status.side_effect = niquests.exceptions.HTTPError()
        mock_session.post.return_value = resp

        with pytest.raises(case.expected_exception) as exc_info:
            loop.run_until_complete(
                fetch_completion(session=mock_session, request={"model": "mistral-large", "messages": []})  # type: ignore[arg-type]
            )

        if case.expected_retry_delay is not None:
            assert getattr(exc_info.value, "retry_delay") == case.expected_retry_delay


class TestFetchEmbeddings:
    @pytest.fixture
    def mock_session(self):
        return AsyncMock(spec=niquests.AsyncSession)

    @staticmethod
    def _setup_response(session, response_data):
        resp = MagicMock()
        resp.json.return_value = response_data
        resp.raise_for_status = MagicMock()
        session.post.return_value = resp

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                EmbeddingsCase(
                    inputs="hello", embeddings_data=[{"embedding": [0.1, 0.2]}], expected_embeddings=[[0.1, 0.2]]
                ),
                id="single_input",
            ),
            pytest.param(
                EmbeddingsCase(
                    inputs=["hello", "world"],
                    embeddings_data=[{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}],
                    expected_embeddings=[[0.1, 0.2], [0.3, 0.4]],
                ),
                id="multiple_inputs",
            ),
        ],
    )
    def test_returns_embeddings(self, loop, mock_session, case: EmbeddingsCase):
        self._setup_response(
            mock_session,
            {"data": case.embeddings_data, "usage": {"total_tokens": 20, "prompt_tokens": 20, "completion_tokens": 0}},
        )

        embeddings, token = loop.run_until_complete(fetch_embeddings(session=mock_session, inputs=case.inputs))

        assert embeddings == case.expected_embeddings
        assert token == {"total": 20, "input": 20, "output": 0}

    def test_calls_correct_endpoint(self, loop, mock_session):
        self._setup_response(
            mock_session,
            {
                "data": [{"embedding": [0.1]}],
                "usage": {"total_tokens": 10, "prompt_tokens": 10, "completion_tokens": 0},
            },
        )

        loop.run_until_complete(fetch_embeddings(session=mock_session, inputs="test", model_name="mistral-embed"))

        call_args = mock_session.post.call_args
        assert call_args[0][0] == "https://api.mistral.ai/v1/embeddings"
        assert call_args[1]["json"]["model"] == "mistral-embed"


class TestFetchAgentCompletion:
    @pytest.fixture
    def mock_session(self):
        return AsyncMock(spec=niquests.AsyncSession)

    @staticmethod
    def _setup_response(session, response_data):
        resp = MagicMock()
        resp.json.return_value = response_data
        resp.raise_for_status = MagicMock()
        session.post.return_value = resp

    def test_returns_content_and_tokens(self, loop, mock_session):
        self._setup_response(
            mock_session,
            {
                "choices": [{"message": {"content": "Agent response"}}],
                "usage": {"total_tokens": 150, "prompt_tokens": 100, "completion_tokens": 50},
            },
        )
        request: AgentsCompletionRequestTypedDict = {"agent_id": "agent-123", "messages": []}

        content, token, _ = loop.run_until_complete(fetch_agent_completion(session=mock_session, request=request))

        assert content == "Agent response"
        assert token == {"total": 150, "input": 100, "output": 50}

    def test_calls_correct_endpoint(self, loop, mock_session):
        self._setup_response(
            mock_session,
            {
                "choices": [{"message": {"content": "Hi"}}],
                "usage": {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5},
            },
        )
        request: AgentsCompletionRequestTypedDict = {"agent_id": "agent-123", "messages": []}

        loop.run_until_complete(fetch_agent_completion(session=mock_session, request=request))

        mock_session.post.assert_called_once_with("https://api.mistral.ai/v1/agents/completions", json=request)
