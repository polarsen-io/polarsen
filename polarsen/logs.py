import logging
from contextvars import ContextVar
from typing import Literal, cast

from tracktolib.logs import init_logging as _tl_init_logging

from polarsen.env import LOGS_FORMAT

_base_logger = logging.getLogger("polarsen")
_worker_logger: ContextVar[logging.LoggerAdapter | None] = ContextVar("worker_logger", default=None)


class _LoggerProxy:
    """Proxy that delegates to WorkerLoggerAdapter if set, otherwise to base logger."""

    def __getattr__(self, name: str):
        logger = _worker_logger.get() or _base_logger
        return getattr(logger, name)


logs = _LoggerProxy()


def init_logs(level: int = logging.INFO, version: str | None = None):
    if LOGS_FORMAT not in ("json", "console"):
        raise ValueError(f"Invalid LOGS_FORMAT {LOGS_FORMAT!r}")
    _tl_init_logging(_base_logger, log_format=cast(Literal["json", "console"], LOGS_FORMAT), version=version or "0.0.0")
    _base_logger.setLevel(level)


class WorkerLoggerAdapter(logging.LoggerAdapter):
    """Adapter to automatically inject worker_id into log messages."""

    def process(self, msg, kwargs):
        _worker_id = self.extra["worker_id"] if self.extra is not None else -1
        _worker_type = self.extra["worker_type"] if self.extra is not None else "Worker"
        return f"[{_worker_type} {_worker_id}] {msg}", kwargs


def set_worker_logger(worker_id: int, worker_type: str = "Worker"):
    """Set the worker logger for the current context."""
    adapter = WorkerLoggerAdapter(_base_logger, {"worker_id": worker_id, "worker_type": worker_type})
    _worker_logger.set(adapter)
