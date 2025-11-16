import logging

logs = logging.getLogger("polarsen")


def init_logs(level: int = logging.INFO):
    global logs
    _stream_handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    _stream_handler.setFormatter(formatter)
    logs.addHandler(_stream_handler)
    logs.setLevel(level)


class WorkerLoggerAdapter(logging.LoggerAdapter):
    """Adapter to automatically inject worker_id into log messages."""

    def process(self, msg, kwargs):
        _worker_id = self.extra["worker_id"] if self.extra is not None else -1
        return f"[Worker {_worker_id}] {msg}", kwargs
