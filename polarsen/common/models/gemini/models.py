"""TypedDict models for Gemini API responses."""

from typing import TypedDict

__all__ = (
    "BatchGenerateContentResponse",
    "BatchJobMetadata",
    "BatchStats",
)


class BatchStats(TypedDict, total=False):
    """Batch stats from mldev API."""

    pendingRequestCount: str
    requestCount: str
    successfulRequestCount: str
    failedRequestCount: str


class BatchJobMetadata(TypedDict, total=False):
    """Metadata for a batch job LRO response."""

    state: str  # BATCH_STATE_*
    displayName: str
    model: str
    createTime: str
    updateTime: str
    startTime: str
    endTime: str
    batchStats: BatchStats


class BatchGenerateContentResponse(TypedDict, total=False):
    """Response from batchGenerateContent endpoint (Long-Running Operation)."""

    name: str
    metadata: BatchJobMetadata
