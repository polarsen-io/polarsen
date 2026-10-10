import math
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

import niquests

if TYPE_CHECKING:
    from google.genai import types

from google.genai.types import JobState

from polarsen.db import UsageToken
from polarsen.env import GEMINI_API_KEY
from polarsen.logs import logs

from ..utils import QuotaExceededError, TooManyRequestsError, retry_async
from .models import BatchGenerateContentResponse

__all__ = (
    "BatchRequest",
    "GeminiBatchJob",
    "_check_resp",
    "cancel_batch_job",
    "fetch_completion",
    "fetch_completions_batch",
    # Batch job management
    "get_batch_job",
    "list_batch_jobs",
    "set_headers",
)

# Job state transformation (BATCH_STATE_* -> JOB_STATE_*)
_JOB_STATE_MAP = {
    "BATCH_STATE_UNSPECIFIED": "JOB_STATE_UNSPECIFIED",
    "BATCH_STATE_PENDING": "JOB_STATE_PENDING",
    "BATCH_STATE_QUEUED": "JOB_STATE_QUEUED",
    "BATCH_STATE_RUNNING": "JOB_STATE_RUNNING",
    "BATCH_STATE_SUCCEEDED": "JOB_STATE_SUCCEEDED",
    "BATCH_STATE_FAILED": "JOB_STATE_FAILED",
    "BATCH_STATE_CANCELLING": "JOB_STATE_CANCELLING",
    "BATCH_STATE_CANCELLED": "JOB_STATE_CANCELLED",
    "BATCH_STATE_EXPIRED": "JOB_STATE_EXPIRED",
}


@dataclass
class GeminiBatchJob:
    """Batch operation from the mldev API; inlined responses stay raw dicts so their request metadata key survives."""

    name: str | None
    state: JobState
    display_name: str | None = None
    model: str | None = None
    create_time: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    update_time: str | None = None
    error: dict | None = None
    responses_file: str | None = None
    inlined_responses: list[dict] = field(default_factory=list)


def _batch_job_from_mldev(data: dict) -> GeminiBatchJob:
    """Transform a raw mldev operation into a GeminiBatchJob."""
    metadata = data.get("metadata", {})
    output = metadata.get("output", {})
    raw_state = metadata.get("state", "BATCH_STATE_UNSPECIFIED")

    return GeminiBatchJob(
        name=data.get("name"),
        display_name=metadata.get("displayName"),
        state=JobState(_JOB_STATE_MAP.get(raw_state, raw_state)),
        model=metadata.get("model"),
        create_time=metadata.get("createTime"),
        start_time=metadata.get("startTime"),
        end_time=metadata.get("endTime"),
        update_time=metadata.get("updateTime"),
        error=data.get("error"),
        responses_file=output.get("responsesFile"),
        inlined_responses=output.get("inlinedResponses", {}).get("inlinedResponses") or [],
    )


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
        raise
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


class BatchRequest:
    """A single request in a batch."""

    def __init__(
        self,
        contents: list[types.Content],
        config: types.GenerateContentConfig | None = None,
        key: str | None = None,
    ):
        self.contents = contents
        self.config = config
        self.key = key


@retry_async()
async def fetch_completions_batch(
    session: niquests.AsyncSession,
    model: str,
    requests: list[BatchRequest],
    display_name: str | None = None,
) -> BatchGenerateContentResponse:
    """Create a batch job using Gemini's batchGenerateContent endpoint.

    Returns the pending job response. Use get_batch_job_status to poll for completion.
    """
    batch_requests = []
    for idx, req in enumerate(requests):
        request_payload: dict = {
            "contents": [c.model_dump(exclude_none=True) for c in req.contents],
        }
        if req.config is not None:
            _config = req.config.model_dump(exclude_none=True)
            _sys_instruction = _config.pop("system_instruction", None)
            request_payload["generation_config"] = _config
            if _sys_instruction is not None:
                request_payload["system_instruction"] = _sys_instruction

        batch_requests.append(
            {
                "request": request_payload,
                "metadata": {"key": req.key or f"request-{idx}"},
            }
        )

    payload = {
        "batch": {
            "displayName": display_name or "batch-request",
            "inputConfig": {
                "requests": {
                    "requests": batch_requests,
                },
            },
        },
    }
    response = await session.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchGenerateContent",
        json=payload,
    )
    return cast(BatchGenerateContentResponse, _check_resp(response))


@retry_async()
async def get_batch_job(
    session: niquests.AsyncSession,
    job_name: str,
) -> GeminiBatchJob:
    """Get the status of a batch job.

    Args:
        session: HTTP session with API key headers set
        job_name: The batch job name (e.g., "batches/123456")

    Returns:
        GeminiBatchJob with current state and progress information
    """
    if not job_name.startswith("batches/"):
        job_name = f"batches/{job_name}"

    response = await session.get(
        f"https://generativelanguage.googleapis.com/v1beta/{job_name}",
    )
    return _batch_job_from_mldev(_check_resp(response))


@retry_async()
async def list_batch_jobs(
    session: niquests.AsyncSession,
    page_size: int = 10,
    page_token: str | None = None,
) -> tuple[list[GeminiBatchJob], str | None]:
    """List batch jobs.

    Args:
        session: HTTP session with API key headers set
        page_size: Maximum number of jobs to return (default 10, max 100)
        page_token: Token for pagination (from previous response)

    Returns:
        Tuple of (jobs list, next_page_token or None)
    """
    params: dict[str, str] = {"pageSize": str(min(page_size, 100))}
    if page_token:
        params["pageToken"] = page_token

    response = await session.get(
        "https://generativelanguage.googleapis.com/v1beta/batches",
        params=params,
    )
    data = _check_resp(response)
    jobs = [_batch_job_from_mldev(op) for op in data.get("operations", [])]
    return jobs, data.get("nextPageToken")


@retry_async()
async def cancel_batch_job(
    session: niquests.AsyncSession,
    job_name: str,
) -> None:
    """Cancel a running or pending batch job.

    Args:
        session: HTTP session with API key headers set
        job_name: The batch job name (e.g., "batches/123456")
    """
    if not job_name.startswith("batches/"):
        job_name = f"batches/{job_name}"

    response = await session.post(
        f"https://generativelanguage.googleapis.com/v1beta/{job_name}:cancel",
    )
    _check_resp(response)
