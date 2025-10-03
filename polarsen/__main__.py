import logging
import os

from piou import Cli

from polarsen.logs import init_logs, logs

try:
    import sentry_sdk
except ImportError:
    sentry_sdk = None

if sentry_sdk is not None and os.getenv("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN"),
        # Add data like request headers and IP for users,
        # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
        send_default_pii=True,
    )


cli = Cli("Polarsen utility commands")

_mode = os.getenv("PROJECT_MODE", "cli").lower()

if _mode != "api":
    cli.add_option("-v", "--verbose", help="Verbosity")
    cli.add_option("-vv", "--verbose2", help="Increased verbosity")

    def on_process(verbose: bool = False, verbose2: bool = False):
        init_logs(logging.DEBUG if verbose2 else logging.INFO if verbose else logging.WARNING)

    cli.set_options_processor(on_process)

if _mode == "cli":
    logs.debug("Running in default mode")
    from .cli import chat_group, ai_group, db_group

    cli.add_command_group(ai_group)
    cli.add_command_group(chat_group)
    cli.add_command_group(db_group)


if _mode == "api":
    from polarsen.api import api_group

    logs.debug("Running in ingest mode")
    cli.add_command_group(api_group)

if _mode == "bot":
    from polarsen.bot import bot_group

    cli.add_command_group(bot_group)

if __name__ == "__main__":
    try:
        cli.run()
    except KeyboardInterrupt:
        logs.info("Ctrl+C detected, exiting...")
