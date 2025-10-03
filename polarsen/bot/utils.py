import functools
from telegram import Update
from .data import User

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

            if not _update_arg.effective_user:
                return
            user = await User.load_user(_update_arg.effective_user)
            if not _update_arg.message:
                return
            await _update_arg.message.reply_text(user.t('error_occurred'))

    return wrapper