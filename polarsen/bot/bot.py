import asyncio
import textwrap
from enum import Enum
from typing import TypeGuard

from niquests import AsyncSession
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    KeyboardButton,
    MenuButton,
    InlineKeyboardMarkup,
    MaybeInaccessibleMessage,
    Message,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)

from polarsen.logs import logs
from .data import User, UserState, ask_question, give_feedback, upload_chat
from .env import API_URI
from .intl import TranslateFn
from .models import Chat
from .models import EmbeddingResult
from .utils import handle_errors


async def start_handler(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /start command.
    """

    if not update.message:
        return
    if not update.effective_user:
        return
    user = User(
        update.effective_user.id,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name,
        lang_code=update.effective_user.language_code or "en",
    )
    logs.info(f"New user: {update.effective_user.first_name} {update.effective_user.last_name}")
    await user.save()

    keyboard = [
        [KeyboardButton(user.t("list_chats_btn")), KeyboardButton(user.t("select_chats_btn"))],
        [KeyboardButton(user.t("ask_question_btn"))],
        [KeyboardButton(user.t("select_ai_btn"))],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(user.t("start_message"), reply_markup=reply_markup)


@handle_errors
async def handle_message(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle incoming messages from the user.
    """

    if not update.effective_user:
        return

    user = await User.load_user(update.effective_user)

    if not update.message:
        logs.warning("Received an update without a message, ignoring.")
        return

    msg = update.message.text

    if user.state == UserState.AWAITING_API_KEY:
        if not msg:
            await update.message.reply_text(user.t("api_key_empty_error"))
            return
        logs.debug(f"User {user.id} is setting API key for {user.selected_model_source} ")
        # Save the API key for the selected model
        user.api_keys[user.selected_model_source] = msg
        user.state = UserState.NORMAL
        await update.message.reply_text(user.t("api_key_saved").format(source=user.selected_model_source.title()))
        return
    elif msg == user.t("list_chats_btn"):
        await update.message.reply_html(fmt_chats(user.chats, user.t))
    elif msg == user.t("select_chats_btn"):
        resp, keyboard = await _select_chat(user)
        await update.message.reply_text(resp, reply_markup=keyboard)
    elif msg == user.t("select_ai_btn"):
        resp, keyboard = await _select_ai(user)
        await update.message.reply_text(resp, reply_markup=keyboard)
    elif msg == user.t("ask_question_btn"):
        if user.selected_chat_id is None:
            await update.message.reply_text(user.t("no_chat_selected"))
        else:
            await update.message.reply_text(user.t("ask_question_prompt").format(chat=user.selected_chat_name))
    elif msg is not None:
        if user.selected_chat_id is None:
            await update.message.reply_text(user.t("no_chat_selected"))
            return
        if user.selected_model is None:
            await update.message.reply_text(user.t("no_model_selected"))
            return
        if user.selected_model_api_key is None:
            # Set user state to awaiting API key and ask for it
            resp, reply_keyboard = _ask_api_key(user)
            await update.message.reply_text(resp, reply_markup=reply_keyboard)
            return

        if user.id is None:
            raise ValueError("User ID is not set. Please load the user from the database.")
        logs.debug(f"User {user.id} is asking question {msg}")
        resp = await ask_question(
            chat_id=user.selected_chat_id,
            model=user.selected_model,
            api_key=user.selected_model_api_key,
            question=msg,
            user_id=user.id,
        )
        response = resp["response"]
        question_id = resp["question_id"]
        user.set_last_question(question_id=question_id, results=resp["results"], response=response)
        feedback_prefix = f"{CallbackPrefix.feedback.value}{question_id}"
        keyboard = [
            [
                InlineKeyboardButton(
                    user.t("show_question_context_btn"), callback_data=CallbackPrefix.show_context.value
                ),
            ],
            [
                InlineKeyboardButton(user.t("feedback_ok_btn"), callback_data=f"{feedback_prefix}-ok"),
                InlineKeyboardButton(user.t("feedback_ko_btn"), callback_data=f"{feedback_prefix}-ko"),
            ],
            [
                InlineKeyboardButton(
                    user.t("feedback_do_not_know_btn"), callback_data=f"{feedback_prefix}-do_not_know"
                ),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_html(response, reply_markup=reply_markup)
    else:
        await update.message.reply_text(user.t("unknown_command"))


@handle_errors
async def handle_file_upload(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    if not update.message:
        return

    user = await User.load_user(update.effective_user)
    if user.state == UserState.AWAITING_CHAT_UPLOAD:
        if user.id is None:
            raise ValueError("User ID is not set. Please load the user from the database.")
        document = update.message.document
        if not document:
            await update.message.reply_text(user.t("upload_chat_error"))
            return
        logs.info(f"User {user.id} is uploading chat {document.file_name}")
        if document.file_name is None:
            raise ValueError("Document file name is None")
        if document.mime_type is None:
            raise ValueError("Document mime type is None")

        file = await document.get_file()
        url = file._get_encoded_url()
        await upload_chat(url, filename=document.file_name, mime_type=document.mime_type, user_id=user.id)

        # Handle chat upload logic here
        # For now, just acknowledge the upload
        user.state = UserState.NORMAL
        await update.message.reply_text(user.t("chat_uploaded").format(chat_name=document.file_name))
        return

@handle_errors
async def stop_handler(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /stop command.
    """
    if not update.effective_user:
        return
    if not update.message:
        return
    user = await User.load_user(update.effective_user)
    await update.message.reply_text(user.t("stop_message"))

@handle_errors
async def help_handler(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /help command.
    """
    if not update.effective_user:
        return
    if not update.message:
        return
    user = await User.load_user(update.effective_user)
    await update.message.reply_text(user.t("help_message"))

@handle_errors
async def select_chat_handler(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /select_chats command."""
    if not update.effective_user:
        return
    if not update.message:
        return
    user = await User.load_user(update.effective_user)
    resp, keyboard = await _select_chat(user)
    await update.message.reply_text(resp, reply_markup=keyboard)

@handle_errors
async def list_chats_handler(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /list_chats command."""
    if not update.effective_user:
        return
    if not update.message:
        return
    user = await User.load_user(update.effective_user)
    await update.message.reply_html(fmt_chats(user.chats, user.t))

@handle_errors
async def select_ai_handler(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /select_ai command."""
    if not update.effective_user:
        return
    if not update.message:
        return
    user = await User.load_user(update.effective_user)
    resp, keyboard = await _select_ai(user)
    await update.message.reply_text(resp, reply_markup=keyboard)

@handle_errors
async def upload_chat_handler(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /add_chat command."""
    if not update.effective_user:
        return
    if not update.message:
        return
    user = await User.load_user(update.effective_user)
    # Set user state to awaiting chat upload
    user.state = UserState.AWAITING_CHAT_UPLOAD
    await user.save()
    keyboard = [[InlineKeyboardButton(user.t("cancel_btn"), callback_data="cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(user.t("upload_chat"), reply_markup=reply_markup)


async def handle_callback_queries(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Parses the CallbackQuery and updates the message text."""
    query = update.callback_query
    if not query or query.data is None:
        return  # No callback query to process
    if not update.effective_user:
        return
    user = await User.load_user(update.effective_user)

    await query.answer()  # Acknowledge the callback query
    # Handle Cancel
    if query.data == CallbackPrefix.cancel.value:
        user.state = UserState.NORMAL
        await query.edit_message_text(user.t("action_cancelled"))
        return
    # Handle Show Context
    elif query.data == CallbackPrefix.show_context.value:
        if _is_accessible_message(query.message) and user.last_question is not None:
            await query.message.reply_html(_fmt_summaries(user.last_question["results"]))
        else:
            logs.warning("Query message is not accessible or last question is None, cannot show context.")
    # Giving a feedback
    elif query.data.startswith(CallbackPrefix.feedback.value):
        feedback = query.data.lstrip(CallbackPrefix.feedback.value)
        question_id, feedback = feedback.split("-")
        await give_feedback(
            question_id=int(question_id),
            feedback=feedback,
        )
        await query.edit_message_reply_markup(None)
        if _is_accessible_message(query.message):
            await query.message.reply_text(user.t("feedback_received"))
        else:
            logs.warning("Query message is not accessible, cannot reply with API key request.")
        # await query.edit_message_text(text=user.t("feedback_received"))
    # Select model
    elif query.data.startswith(CallbackPrefix.model.value):
        model_name = query.data.split(CallbackPrefix.model.value)[1]
        user.selected_model = model_name
        await query.edit_message_text(text=user.t("selected_model").format(model=model_name))
        await user.save()
        if not user.selected_model_api_key:
            resp, reply_keyboard = _ask_api_key(user)
            if query.message is None:
                raise ValueError("Query message is None, cannot reply.")
            if _is_accessible_message(query.message):
                await query.message.reply_text(resp, reply_markup=reply_keyboard)
            else:
                logs.warning("Query message is not accessible, cannot reply with API key request.")
    elif query.data.startswith(CallbackPrefix.chat.value):
        selected_chat_id = int(query.data.lstrip(CallbackPrefix.model.value))
        user.selected_chat_id = selected_chat_id
        await query.edit_message_text(text=user.t("selected_chat").format(chat=user.selected_chat_name))
        await user.save()


# Utils


def _is_accessible_message(message: MaybeInaccessibleMessage | None) -> TypeGuard[Message]:
    if message is None:
        return False
    return message.is_accessible


def fmt_chats(chats: list[Chat], t: TranslateFn) -> str:
    html = ""
    for chat in chats:
        html += textwrap.dedent(
            """
        - {id} <i>{name}</i> ({last_message}: {cutoff_date})
        """.format(**chat, last_message=t("last_message"))
        )
    msg = t("list_chats_resp").format(chats=html)
    return msg


def _fmt_summaries(summaries: list[EmbeddingResult]) -> str:
    if not summaries:
        return ""
    return "\n\n" + textwrap.dedent("""
    <b>Context results:</b>
    {}
    """).format("\n".join(["\n\n <b>{title}</b> ({day})\n{summary}".format(**s) for s in summaries]))


class CallbackPrefix(Enum):
    model = "select-model-"
    chat = "select-"
    feedback = "feedback-"
    cancel = "cancel"
    show_context = "showcontext"


async def _select_chat(user: User):
    keyboard = [
        [InlineKeyboardButton(chat["name"], callback_data=CallbackPrefix.chat.value + str(chat["id"]))]
        for chat in user.chats
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    return user.t("select_chats_btn"), reply_markup


async def _select_ai(user: User):
    async with AsyncSession() as session:
        resp = await session.get(f"{API_URI}/models")
        resp.raise_for_status()
    models = resp.json()

    buttons = []
    for model in models:
        _name = model["name"]
        if _name == user.selected_model:
            _name = f"✅ {_name}"
        if model["source"] in user.api_keys:
            _name += " 🔑"
        _button = InlineKeyboardButton(_name, callback_data=CallbackPrefix.model.value + model["name"])
        buttons.append([_button])

    reply_markup = InlineKeyboardMarkup(buttons)
    return user.t("select_ai_btn"), reply_markup


def _ask_api_key(user: User) -> tuple[str, InlineKeyboardMarkup]:
    # Set user state to awaiting API key and ask for it
    user.state = UserState.AWAITING_API_KEY
    # Create cancel keyboard
    keyboard = [[InlineKeyboardButton(user.t("cancel_btn"), callback_data="cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    return user.t("enter_api_key").format(source=user.selected_model_source.title()), reply_markup


_COMMANDS = {
    "start": "Start the bot",
    "help": "Show help message",
    "stop": "Stop the bot",
    "select_chats": "Select a chat",
    "list_chats": "List all chats",
    "select_ai": "Select an AI model",
    "add_chat": "Add a new chat",
}


async def wait_forever():
    await asyncio.Event().wait()


MENU_BTN = MenuButton(
    type="commands",
    api_kwargs=None,  # No additional API kwargs
)


async def run_bot(token: str, shutdown_event: asyncio.Event) -> None:
    app = ApplicationBuilder().token(token).build()
    handlers = [
        CommandHandler(["start"], start_handler),
        CommandHandler(["help"], help_handler),
        CommandHandler(["stop"], stop_handler),
        CommandHandler(["select_chats"], select_chat_handler),
        CommandHandler(["list_chats"], list_chats_handler),
        CommandHandler(["select_ai"], select_ai_handler),
        CommandHandler(["add_chat"], upload_chat_handler),
        CallbackQueryHandler(handle_callback_queries),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
        MessageHandler(filters.Document.MimeType("application/json"), handle_file_upload),  # Handle document uploads
    ]
    for handler in handlers:
        if isinstance(handler, CommandHandler):
            if not (handler.commands & _COMMANDS.keys()):
                raise ValueError(f"Could not find command {handler.commands} in _COMMANDS")

    for _handler in handlers:
        app.add_handler(_handler)
    logs.info("Initializing bot")
    # Run application and webserver together
    async with app:
        await app.bot.set_chat_menu_button(menu_button=MENU_BTN)
        if app.updater is None:
            raise ValueError("Application updater is not initialized")
        await app.updater.start_polling()
        logs.info("Bot started")
        await app.start()
        logs.info("Waiting forever")
        try:
            await shutdown_event.wait()
            logs.info("Shutdown event received")
        finally:
            logs.info("Saving users infos")
            await User.save_all_users()
            logs.info("Users infos saved")
            logs.info("Stopping bot...")
            await app.updater.stop()
            await app.stop()
            logs.info("Bot stopped")


def show_commands():
    """
    Show the available commands for the bot.
    """
    print("\n".join(f"{cmd} - {desc}" for cmd, desc in _COMMANDS.items()))
