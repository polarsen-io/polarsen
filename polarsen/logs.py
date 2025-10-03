import logging

logs = logging.getLogger("polarsen")


def init_logs(level: int = logging.INFO):
    global logs
    _stream_handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    _stream_handler.setFormatter(formatter)
    logs.addHandler(_stream_handler)
    logs.setLevel(level)
