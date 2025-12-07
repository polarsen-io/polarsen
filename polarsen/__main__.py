import logging
import os

from piou import Cli

from polarsen.logs import init_logs, logs
from polarsen.telemetry.sentry import init_sentry

init_sentry()

cli = Cli("Polarsen utility commands")

_mode = os.getenv("PROJECT_MODE", "local").lower()

if _mode != "api":
    cli.add_option("-v", "--verbose", help="Verbosity")
    cli.add_option("-vv", "--verbose2", help="Increased verbosity")

    def on_process(verbose: bool = False, verbose2: bool = False):
        init_logs(logging.DEBUG if verbose2 else logging.INFO if verbose else logging.WARNING)

    cli.set_options_processor(on_process)

if _mode in ("cli", "local"):
    logs.debug("Running in CLI mode")
    from .cli import chat_group, ai_group, db_group

    cli.add_command_group(ai_group)
    cli.add_command_group(chat_group)
    cli.add_command_group(db_group)


if _mode in ("api", "local"):
    from polarsen.api import api_group

    logs.debug("Running in API mode")
    cli.add_command_group(api_group)

if _mode in ("bot", "local"):
    from polarsen.bot import bot_group

    logs.debug("Running in BOT mode")
    cli.add_command_group(bot_group)

if __name__ == "__main__":
    try:
        cli.run()
    except KeyboardInterrupt:
        logs.info("Ctrl+C detected, exiting...")
