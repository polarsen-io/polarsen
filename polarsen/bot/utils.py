import functools
from telegram import Update
from .data import User

from polarsen.telemetry.sentry import capture_exception
from polarsen.logs import logs

__all__ = ("handle_errors",)


def handle_errors(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if not args:
                raise ValueError("Expected at least one argument for the decorated function")
            _update_arg = args[0]
            if not isinstance(_update_arg, Update):
                raise ValueError("The first argument must be an instance of telegram.Update")

            if capture_exception is not None:
                capture_exception(e)
            else:
                logs.exception("Failed to load user")

            if not _update_arg.effective_user:
                logs.warning("Failed to load user")
                return None

            user = await User.load_user(_update_arg.effective_user)
            if not _update_arg.message:
                logs.warning("No message to reply to")
                return None

            await _update_arg.message.reply_text(user.t("error_occurred"))

    return wrapper
