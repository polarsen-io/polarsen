from http import HTTPStatus
from typing import NamedTuple
from unittest.mock import AsyncMock, MagicMock

import niquests
import pytest
from google.genai.types import JobState

from polarsen.common.models.gemini.fetch import (
    HEADER_KEY,
    _batch_job_from_mldev,
    _parse_retry_delay,
    cancel_batch_job,
    fetch_completion,
    get_batch_job,
    list_batch_jobs,
    set_headers,
)
from polarsen.common.models.utils import QuotaExceededError, TooManyRequestsError


class CompletionCase(NamedTuple):
    response_data: dict
    expected_content: str
    expected_cached: int


class ErrorCase(NamedTuple):
    response_data: dict
    status_code: int
    expected_exception: type[Exception]
    expected_retry_delay: int | None = None
    check_body: bool = False


class TestSetHeaders:
    def test_sets_api_key_from_param(self):
        session = MagicMock(spec=niquests.Session)
        session.headers = {}

        result = set_headers(session, api_key="test-key")

        assert result == HEADER_KEY
        assert session.headers[HEADER_KEY] == "test-key"

    def test_raises_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr("polarsen.common.models.gemini.fetch.GEMINI_API_KEY", None)
        session = MagicMock(spec=niquests.Session)
        session.headers = {}

        with pytest.raises(ValueError, match="GEMINI_API_KEY is not set"):
            set_headers(session, api_key=None)


class TestParseRetryDelay:
    @pytest.mark.parametrize(
        "delay_str,expected",
        [
            pytest.param("30s", 30, id="integer"),
            pytest.param("48.24916365s", 49, id="decimal_rounds_up"),
            pytest.param("1.1s", 2, id="small_decimal"),
            pytest.param("0.5s", 1, id="less_than_one"),
        ],
    )
    def test_parses_delay(self, delay_str, expected):
        assert _parse_retry_delay(delay_str) == expected


class TestFetchCompletion:
    @pytest.fixture
    def mock_session(self):
        return AsyncMock(spec=niquests.AsyncSession)

    @pytest.fixture
    def mock_contents(self):
        from google.genai.types import Content, Part

        return [Content(role="user", parts=[Part.from_text(text="Say hello")])]

    @pytest.fixture
    def mock_config(self):
        from google.genai.types import GenerateContentConfig

        return GenerateContentConfig(temperature=0.7)

    def _setup_response(self, session, response_data):
        resp = MagicMock()
        resp.json.return_value = response_data
        resp.raise_for_status = MagicMock()
        session.post.return_value = resp

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                CompletionCase(
                    response_data={
                        "candidates": [{"content": {"parts": [{"text": "Hello, world!"}]}}],
                        "usageMetadata": {
                            "totalTokenCount": 100,
                            "promptTokenCount": 50,
                            "candidatesTokenCount": 50,
                            "cachedContentTokenCount": 10,
                        },
                    },
                    expected_content="Hello, world!",
                    expected_cached=10,
                ),
                id="with_cached_tokens",
            ),
            pytest.param(
                CompletionCase(
                    response_data={
                        "candidates": [{"content": {"parts": [{"text": "Response"}]}}],
                        "usageMetadata": {
                            "totalTokenCount": 100,
                            "promptTokenCount": 50,
                            "candidatesTokenCount": 50,
                        },
                    },
                    expected_content="Response",
                    expected_cached=0,
                ),
                id="without_cached_tokens",
            ),
        ],
    )
    def test_returns_content_and_tokens(self, loop, mock_session, mock_contents, mock_config, case: CompletionCase):
        self._setup_response(mock_session, case.response_data)

        content, token, _ = loop.run_until_complete(
            fetch_completion(session=mock_session, model="gemini-2.5-flash", contents=mock_contents, config=mock_config)
        )

        assert content == case.expected_content
        assert token["total"] == 100
        assert token["input"] == 50
        assert token["output"] == 50
        assert token["cached"] == case.expected_cached

    def test_calls_correct_endpoint(self, loop, mock_session, mock_contents, mock_config):
        self._setup_response(
            mock_session,
            {
                "candidates": [{"content": {"parts": [{"text": "Hello"}]}}],
                "usageMetadata": {"totalTokenCount": 10, "promptTokenCount": 5, "candidatesTokenCount": 5},
            },
        )

        loop.run_until_complete(
            fetch_completion(session=mock_session, model="gemini-2.5-flash", contents=mock_contents, config=mock_config)
        )

        mock_session.post.assert_called_once()
        url = mock_session.post.call_args[0][0]
        assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

    def test_includes_system_instruction(self, loop, mock_session, mock_contents):
        from google.genai.types import Content, GenerateContentConfig, Part

        self._setup_response(
            mock_session,
            {
                "candidates": [{"content": {"parts": [{"text": "Hello"}]}}],
                "usageMetadata": {"totalTokenCount": 10, "promptTokenCount": 5, "candidatesTokenCount": 5},
            },
        )

        config = GenerateContentConfig(
            temperature=0.7,
            system_instruction=Content(parts=[Part.from_text(text="You are helpful")]),
        )

        loop.run_until_complete(
            fetch_completion(session=mock_session, model="gemini-2.5-flash", contents=mock_contents, config=config)
        )

        payload = mock_session.post.call_args[1]["json"]
        assert "system_instruction" in payload

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                ErrorCase(
                    response_data={
                        "error": {
                            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30s"}]
                        }
                    },
                    status_code=HTTPStatus.TOO_MANY_REQUESTS,
                    expected_exception=TooManyRequestsError,
                    expected_retry_delay=30,
                ),
                id="too_many_requests",
            ),
            pytest.param(
                ErrorCase(
                    response_data={
                        "error": {
                            "details": [
                                {
                                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                    "violations": [{"subject": "test"}],
                                }
                            ]
                        }
                    },
                    status_code=HTTPStatus.TOO_MANY_REQUESTS,
                    expected_exception=QuotaExceededError,
                    check_body=True,
                ),
                id="quota_exceeded",
            ),
        ],
    )
    def test_error_handling(self, monkeypatch, loop, mock_session, mock_contents, mock_config, case: ErrorCase):
        monkeypatch.setattr("polarsen.common.models.utils.asyncio.sleep", AsyncMock())

        resp = MagicMock()
        resp.status_code = case.status_code
        resp.json.return_value = case.response_data
        resp.raise_for_status.side_effect = niquests.exceptions.HTTPError()
        mock_session.post.return_value = resp

        with pytest.raises(case.expected_exception) as exc_info:
            loop.run_until_complete(
                fetch_completion(
                    session=mock_session, model="gemini-2.5-flash", contents=mock_contents, config=mock_config
                )
            )

        if case.expected_retry_delay is not None:
            assert isinstance(exc_info.value, TooManyRequestsError)
            assert exc_info.value.retry_delay == case.expected_retry_delay
        if case.check_body:
            assert isinstance(exc_info.value, QuotaExceededError)
            assert exc_info.value.body == case.response_data


class TestBatchJobFromMldev:
    @pytest.mark.parametrize(
        "raw_state,expected",
        [
            pytest.param("BATCH_STATE_PENDING", JobState.JOB_STATE_PENDING, id="pending"),
            pytest.param("BATCH_STATE_RUNNING", JobState.JOB_STATE_RUNNING, id="running"),
            pytest.param("BATCH_STATE_SUCCEEDED", JobState.JOB_STATE_SUCCEEDED, id="succeeded"),
            pytest.param("BATCH_STATE_CANCELLED", JobState.JOB_STATE_CANCELLED, id="cancelled"),
            pytest.param("JOB_STATE_FAILED", JobState.JOB_STATE_FAILED, id="already_job_state_passthrough"),
            pytest.param(None, JobState.JOB_STATE_UNSPECIFIED, id="missing_state"),
        ],
    )
    def test_maps_state(self, raw_state, expected):
        metadata = {} if raw_state is None else {"state": raw_state}

        assert _batch_job_from_mldev({"name": "batches/1", "metadata": metadata}).state == expected

    @pytest.mark.parametrize(
        "output,expected_file,expected_inlined",
        [
            pytest.param({}, None, [], id="no_output"),
            pytest.param({"responsesFile": "files/abc"}, "files/abc", [], id="responses_file"),
            pytest.param(
                {"inlinedResponses": {"inlinedResponses": [{"metadata": {"key": "2024-01-01"}, "response": {}}]}},
                None,
                [{"metadata": {"key": "2024-01-01"}, "response": {}}],
                id="inlined_keep_metadata_key",
            ),
        ],
    )
    def test_reads_output(self, output, expected_file, expected_inlined):
        job = _batch_job_from_mldev(
            {"name": "batches/1", "metadata": {"state": "BATCH_STATE_SUCCEEDED", "output": output}}
        )

        assert job.responses_file == expected_file
        assert job.inlined_responses == expected_inlined


class TestBatchJobPrefix:
    @pytest.mark.parametrize(
        "func,verb,job_name,expected_url",
        [
            pytest.param(get_batch_job, "get", "123", "batches/123", id="get_adds_prefix"),
            pytest.param(get_batch_job, "get", "batches/123", "batches/123", id="get_keeps_prefix"),
            pytest.param(cancel_batch_job, "post", "123", "batches/123:cancel", id="cancel_adds_prefix"),
            pytest.param(cancel_batch_job, "post", "batches/123", "batches/123:cancel", id="cancel_keeps_prefix"),
        ],
    )
    def test_prefix(self, loop, func, verb, job_name, expected_url):
        session = AsyncMock(spec=niquests.AsyncSession)
        resp = MagicMock()
        resp.json.return_value = {"name": "batches/123", "metadata": {}}
        getattr(session, verb).return_value = resp

        loop.run_until_complete(func(session, job_name))

        getattr(session, verb).assert_called_once_with(
            f"https://generativelanguage.googleapis.com/v1beta/{expected_url}"
        )


class TestListBatchJobs:
    @pytest.mark.parametrize(
        "kwargs,expected_params",
        [
            pytest.param({}, {"pageSize": "10"}, id="default"),
            pytest.param({"page_size": 200}, {"pageSize": "100"}, id="capped_at_100"),
            pytest.param({"page_token": "tok"}, {"pageSize": "10", "pageToken": "tok"}, id="page_token"),
        ],
    )
    def test_params_and_result(self, loop, kwargs, expected_params):
        session = AsyncMock(spec=niquests.AsyncSession)
        resp = MagicMock()
        resp.json.return_value = {
            "operations": [{"name": "batches/1", "metadata": {"state": "BATCH_STATE_RUNNING"}}],
            "nextPageToken": "next",
        }
        session.get.return_value = resp

        jobs, next_token = loop.run_until_complete(list_batch_jobs(session, **kwargs))

        session.get.assert_called_once_with(
            "https://generativelanguage.googleapis.com/v1beta/batches", params=expected_params
        )
        assert [j.name for j in jobs] == ["batches/1"]
        assert jobs[0].state == JobState.JOB_STATE_RUNNING
        assert next_token == "next"
