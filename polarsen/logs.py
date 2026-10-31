import logging
from contextvars import ContextVar

_base_logger = logging.getLogger("polarsen")
_worker_logger: ContextVar[logging.LoggerAdapter | None] = ContextVar("worker_logger", default=None)


class _LoggerProxy:
    """Proxy that delegates to WorkerLoggerAdapter if set, otherwise to base logger."""

    def __getattr__(self, name: str):
        logger = _worker_logger.get() or _base_logger
        return getattr(logger, name)


logs = _LoggerProxy()


def init_logs(level: int = logging.INFO):
    _stream_handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    _stream_handler.setFormatter(formatter)
    _base_logger.addHandler(_stream_handler)
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
