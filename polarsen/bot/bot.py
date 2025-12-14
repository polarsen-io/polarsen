import asyncio
import textwrap
from enum import Enum
from typing import TypeGuard

import telegram.error
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
    BotCommand,
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
from .intl import TranslateFn, i18n
from .models import Chat, ChatUpload
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
        # If there's a pending question, ask it now
        if user.pending_question:
            pending = user.pending_question
            user.pending_question = None
            await _ask_and_respond(user, pending, update.message)
        return
    elif msg == user.t("list_chats_btn"):
        await _list_chats(user, update.message)
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
            # Store the question and ask for API key
            user.pending_question = msg
            resp, reply_keyboard = _ask_api_key(user)
            await update.message.reply_text(resp, reply_markup=reply_keyboard)
            return

        await _ask_and_respond(user, msg, update.message)
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

        try:
            file = await document.get_file()
        except telegram.error.BadRequest as e:
            logs.debug(f"Failed to get file for document {document.file_name}: {e}")
            await update.message.reply_text(
                user.t("upload_chat_error") + user.t("error_detail").format(detail=e.message)
            )
            return

        # Send a processing message before the upload starts
        await update.message.reply_text(user.t("chat_uploading").format(chat_name=document.file_name))

        url = file._get_encoded_url()
        await upload_chat(url, filename=document.file_name, mime_type=document.mime_type, user_id=user.id)

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
    await _list_chats(user, update.message)


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


@handle_errors
async def ask_handler(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /ask command. Usage: /ask <question>"""
    if not update.effective_user:
        return
    if not update.message:
        return
    user = await User.load_user(update.effective_user)

    # Extract question from command (everything after /ask)
    if not update.message.text:
        return
    parts = update.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(user.t("ask_usage"))
        return
    question = parts[1].strip()

    # Check prerequisites
    if user.selected_chat_id is None:
        await update.message.reply_text(user.t("no_chat_selected"))
        return
    if user.selected_model is None:
        await update.message.reply_text(user.t("no_model_selected"))
        return
    if user.selected_model_api_key is None:
        # Store the question and ask for API key
        user.pending_question = question
        resp, reply_keyboard = _ask_api_key(user)
        await update.message.reply_text(resp, reply_markup=reply_keyboard)
        return

    await _ask_and_respond(user, question, update.message)


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
            await query.message.reply_html(_fmt_summaries(user.last_question["results"], user.t))
        elif _is_accessible_message(query.message):
            logs.warning("Last question is None, cannot show context.")
            await query.message.reply_text(user.t("no_context_available"))
        else:
            logs.warning("Query message is not accessible, cannot show context.")
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
        selected_chat_id = int(query.data.lstrip(CallbackPrefix.chat.value))
        user.selected_chat_id = selected_chat_id
        await query.edit_message_text(text=user.t("selected_chat").format(chat=user.selected_chat_name))
        await user.save()


# Utils


def _is_accessible_message(message: MaybeInaccessibleMessage | None) -> TypeGuard[Message]:
    """Check if a message is accessible and narrow its type to Message.

    Telegram messages may become inaccessible (e.g., deleted or in restricted chats).
    This type guard filters out None and inaccessible messages for safe handling.
    """
    if message is None:
        return False
    return message.is_accessible


async def _list_chats(user: User, message: Message) -> None:
    """Send the list of chats to the user. Used by both /list_chats and 'View groups' button."""
    if not user.chats and not user.uploads:
        await message.reply_text(user.t("no_chats"))
        return
    html = fmt_chats(chats=user.chats, uploads=user.uploads, t=user.t)
    if html is None:
        return
    await message.reply_html(html)


async def _ask_and_respond(user: User, question: str, message: Message) -> None:
    """Ask a question and send the response. Used after API key is set or when asking directly."""
    if user.id is None:
        raise ValueError("User ID is not set. Please load the user from the database.")
    if user.selected_chat_id is None:
        raise ValueError("No chat selected.")
    if user.selected_model is None:
        raise ValueError("No model selected.")
    if user.selected_model_api_key is None:
        raise ValueError("No API key set.")

    logs.debug(f"User {user.id} is asking question {question}")
    resp = await ask_question(
        chat_id=user.selected_chat_id,
        model=user.selected_model,
        question=question,
        user_id=user.id,
    )
    response = resp["response"]
    question_id = resp["question_id"]
    user.set_last_question(question_id=question_id, results=resp["results"], response=response)
    feedback_prefix = f"{CallbackPrefix.feedback.value}{question_id}"
    keyboard = [
        [
            InlineKeyboardButton(user.t("show_question_context_btn"), callback_data=CallbackPrefix.show_context.value),
        ],
        [
            InlineKeyboardButton(user.t("feedback_ok_btn"), callback_data=f"{feedback_prefix}-ok"),
            InlineKeyboardButton(user.t("feedback_ko_btn"), callback_data=f"{feedback_prefix}-ko"),
        ],
        [
            InlineKeyboardButton(user.t("feedback_do_not_know_btn"), callback_data=f"{feedback_prefix}-do_not_know"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await message.reply_html(response, reply_markup=reply_markup)


def fmt_chats(chats: list[Chat], uploads: list[ChatUpload], t: TranslateFn) -> str | None:
    # Processing Chats
    html = ""
    for chat in chats:
        html += textwrap.dedent(
            """
        - {id} <i>{name}</i> ({last_message}: {cutoff_date})
        """.format(**chat, last_message=t("last_message"))
        )
    msg, upload_msg = None, None
    if html:
        msg = t("list_chats_resp").format(chats=html)

    # Processing Uploads
    html = ""
    for upload in uploads:
        if upload["processed_at"] is not None:
            continue
        html += textwrap.dedent(
            """
        - {file_id} <i>{filename}</i> ({chat_type}, {created_at})
        """.format(
                created_at=upload["created_at"].split("T")[0],
                chat_type=upload["chat_type"],
                file_id=upload["file_id"],
                filename=upload["filename"],
            )
        )
    if html:
        upload_msg = t("list_uploads_resp").format(uploads=html)

    # Combine messages
    _final_msg = ""
    if msg:
        _final_msg += msg
    if upload_msg:
        if _final_msg:
            _final_msg += "\n\n"
        _final_msg += upload_msg
    return _final_msg if _final_msg else None


def _fmt_summaries(summaries: list[EmbeddingResult], t: TranslateFn) -> str:
    """Format embedding search results for display.

    Returns a translated message with context results, or a 'no context available'
    message when summaries list is empty.
    """
    if not summaries:
        return t("no_context_available")
    formatted = "\n".join(["\n\n<b>{title}</b> ({day})\n{summary}".format(**s) for s in summaries])
    return f"\n\n{t('context_results_header')}\n{formatted}"


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


# Command names used for validation (descriptions come from commands_i18n)
_COMMAND_NAMES = {"start", "help", "stop", "select_chats", "list_chats", "select_ai", "add_chat", "ask"}


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
        CommandHandler(["ask"], ask_handler),
        CallbackQueryHandler(handle_callback_queries),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
        MessageHandler(filters.Document.MimeType("application/json"), handle_file_upload),  # Handle document uploads
    ]
    for handler in handlers:
        if isinstance(handler, CommandHandler):
            if not (handler.commands & _COMMAND_NAMES):
                raise ValueError(f"Could not find command {handler.commands} in _COMMAND_NAMES")

    for _handler in handlers:
        app.add_handler(_handler)
    logs.info("Initializing bot")
    # Run application and webserver together
    async with app:
        # Set commands for each supported language
        for lang in i18n.languages:
            commands = i18n.get_commands(lang)
            bot_commands = [BotCommand(cmd, desc) for cmd, desc in commands.items()]
            if lang == "en":
                # English is the default (no language_code)
                await app.bot.set_my_commands(bot_commands)
            else:
                await app.bot.set_my_commands(bot_commands, language_code=lang)
            logs.debug(f"Set commands for language: {lang}")
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


def show_commands(lang: str = "en"):
    """
    Show the available commands for the bot in the specified language.
    """
    commands = i18n.get_commands(lang)
    print("\n".join(f"{cmd} - {desc}" for cmd, desc in commands.items()))
