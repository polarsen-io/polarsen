import importlib
from pathlib import Path

from piou import CommandGroup, Option

from polarsen.logs import logs
from . import bot
from .env import TELEGRAM_BOT_TOKEN

__all__ = ("bot_group",)

bot_group = CommandGroup("bot", help="Bot commands")
_cur_dir = Path(__file__).parent


@bot_group.command("start", help="Run the bot")
async def run_bot_command(
    token: str = Option(TELEGRAM_BOT_TOKEN or "", "--token", help="Bot token"),
    reload: bool = Option(False, "--reload", help="Enable auto-reload on file changes"),
):
    """
    Run the bot with the specified token and options, restarting on file changes if reload is enabled.
    """
    import asyncio
    import signal

    bot_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    bot_task = asyncio.create_task(bot.run_bot(token, shutdown_event=bot_event))

    try:
        if reload:
            from watchfiles import awatch

            watcher_event = asyncio.Event()

            def signal_handler(signum, frame):
                loop.call_soon_threadsafe(watcher_event.set)

            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, signal_handler)

            async for _ in awatch(_cur_dir, stop_event=watcher_event):
                logs.warning("File change detected, restarting bot...")
                loop.call_soon_threadsafe(bot_event.set)
                await bot_task
                importlib.reload(bot)
                bot_task = asyncio.create_task(bot.run_bot(token, shutdown_event=bot_event))
        else:

            def signal_handler(signum, frame):
                loop.call_soon_threadsafe(bot_event.set)

            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, signal_handler)

            await bot_task
    finally:
        if bot_task and not bot_task.done():
            bot_event.set()
            await bot_task

    logs.info("Bot stopped gracefully.")


bot_group.add_command(bot.show_commands, "show-commands", help="Show bot commands")
