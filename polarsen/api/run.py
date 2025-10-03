import os

from piou import CommandGroup
import uvicorn
from piou import Option, Derived
import logging

__all__ = ("api_group",)

api_group = CommandGroup("api", help="API commands")


def get_log_lvl(
    verbose: bool = Option(False, "-v", "--verbose", help="Verbosity"),
    verbose2: bool = Option(False, "-vv", "--verbose2", help="Increased Verbosity"),
):
    return logging.DEBUG if verbose2 else logging.INFO if verbose else logging.WARNING


@api_group.command(
    "start",
    help="Start the API server",
)
def start_api(
    host: str = Option("0.0.0.0", "--host", help="API Host"),
    port: int = Option(5050, "--port", help="API port"),
    log_lvl: int = Derived(get_log_lvl),
    dev: bool = Option(False, "--dev", help="Run in dev mode"),
    access_log: bool = Option(False, "--show-access-log", help="Enable access log"),
):
    os.environ["_PG_URL"] = os.environ["PG_DSN"]
    uvicorn.run(
        "polarsen.api.main:app",
        host=host,
        port=port,
        reload=dev,
        access_log=access_log,
        log_level=log_lvl,
        # log_config=log_config,
        # use_colors=use_colors,
        # reload_includes=["api"],
    )
