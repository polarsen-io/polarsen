from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update, User as TgUser, Message

from polarsen.bot import bot as bot_module
from polarsen.bot.bot import (
    list_chats_handler,
    handle_message,
    fmt_chats,
    _list_chats,
)
from polarsen.bot.data import User, UserState
from polarsen.bot.models import Chat, ChatUpload
from polarsen.bot.intl import TranslateFn


# --- Fixtures ---


@pytest.fixture
def tg_user():
    """Create a mock Telegram User object."""
    user = MagicMock(spec=TgUser)
    user.id = 12345
    user.first_name = "Test"
    user.last_name = "User"
    user.language_code = "en"
    return user


@pytest.fixture
def mock_message():
    """Create a mock Message that tracks reply calls."""
    message = AsyncMock(spec=Message)
    message.reply_html = AsyncMock()
    message.reply_text = AsyncMock()
    return message


@pytest.fixture
def mock_update(tg_user, mock_message):
    """Create a mock Update with user and message."""
    update = MagicMock(spec=Update)
    update.effective_user = tg_user
    update.message = mock_message
    return update


@pytest.fixture
def mock_context():
    """Create a mock context."""
    return MagicMock()


@pytest.fixture
def mock_bot_user():
    """Create a mock bot User with sample chats."""
    user = MagicMock(spec=User)
    user.chats = [
        Chat(id=1, name="Test Group", cutoff_date="2024-01-15"),
        Chat(id=2, name="Another Group", cutoff_date="2024-02-20"),
    ]
    user.uploads = []
    user.t = cast(
        TranslateFn,
        lambda key, *args, **kwargs: {
            "list_chats_resp": "<b>Available groups:</b>{chats}",
            "last_message": "last message",
            "list_chats_btn": "View groups",
            "no_chats": "No groups available...",
        }.get(key, key),
    )
    return user


# --- Tests ---


class TestListChatsSharedBehavior:
    """
    Test that 'View Groups' button and /list_chats command
    call the same endpoint and produce identical output.
    """

    def test_list_chats_command_loads_user(self, mock_update, mock_context, mock_bot_user, loop, monkeypatch):
        """Verify /list_chats calls User.load_user()."""
        mock_load_user = AsyncMock(return_value=mock_bot_user)
        monkeypatch.setattr(bot_module.User, "load_user", mock_load_user)

        loop.run_until_complete(list_chats_handler(mock_update, mock_context))

        mock_load_user.assert_called_once_with(mock_update.effective_user)

    def test_view_groups_button_loads_user(self, mock_update, mock_context, mock_bot_user, loop, monkeypatch):
        """Verify 'View groups' button calls User.load_user()."""
        mock_load_user = AsyncMock(return_value=mock_bot_user)
        monkeypatch.setattr(bot_module.User, "load_user", mock_load_user)
        mock_update.message.text = "View groups"

        loop.run_until_complete(handle_message(mock_update, mock_context))

        mock_load_user.assert_called_once_with(mock_update.effective_user)

    def test_both_handlers_produce_same_output(self, mock_update, mock_context, mock_bot_user, loop, monkeypatch):
        """
        Core test: verify both entry points produce identical HTML response.
        This ensures both the button and command call the same underlying logic.
        """
        mock_load_user = AsyncMock(return_value=mock_bot_user)
        monkeypatch.setattr(bot_module.User, "load_user", mock_load_user)

        # 1. Call /list_chats command
        loop.run_until_complete(list_chats_handler(mock_update, mock_context))
        command_response = mock_update.message.reply_html.call_args[0][0]

        # Reset mock for second call
        mock_update.message.reply_html.reset_mock()

        # 2. Call via "View groups" button
        mock_update.message.text = "View groups"
        loop.run_until_complete(handle_message(mock_update, mock_context))
        button_response = mock_update.message.reply_html.call_args[0][0]

        # 3. Assert identical output
        assert command_response == button_response
        assert "Test Group" in command_response
        assert "Another Group" in command_response

    def test_no_chats_button_sends_message(self, mock_update, mock_context, mock_bot_user, loop, monkeypatch):
        """Button sends 'no_chats' message when user has no chats."""
        mock_bot_user.chats = []
        mock_bot_user.uploads = []
        mock_load_user = AsyncMock(return_value=mock_bot_user)
        monkeypatch.setattr(bot_module.User, "load_user", mock_load_user)

        mock_update.message.text = "View groups"
        loop.run_until_complete(handle_message(mock_update, mock_context))

        mock_update.message.reply_text.assert_called_with("No groups available...")


class TestFmtChats:
    """Unit tests for the fmt_chats formatting function."""

    @staticmethod
    def _make_t(translations: dict[str, str]) -> TranslateFn:
        """Create a translation function from a dict."""
        return cast(TranslateFn, lambda k, *args, **kwargs: translations.get(k, k))

    def test_formats_chats_correctly(self):
        """Test that chats are formatted as expected."""
        chats: list[Chat] = [Chat(id=1, name="My Group", cutoff_date="2024-01-01")]
        uploads: list[ChatUpload] = []
        t = self._make_t({"list_chats_resp": "<b>Groups:</b>{chats}", "last_message": "last"})

        result = fmt_chats(chats, uploads, t)

        assert result is not None
        assert "<b>Groups:</b>" in result
        assert "My Group" in result
        assert "2024-01-01" in result

    def test_returns_none_for_empty(self):
        """Test that empty chats/uploads returns None."""
        t = self._make_t({})
        result = fmt_chats([], [], t)
        assert result is None

    def test_formats_uploads_correctly(self):
        """Test that pending uploads are formatted."""
        chats: list[Chat] = []
        uploads: list[ChatUpload] = [
            ChatUpload(
                file_id=42,
                filename="export.json",
                file_path="/path/to/file",
                chat_type="telegram",
                created_at="2024-03-15T10:30:00",
                processed_at=None,
            )
        ]
        t = self._make_t({"list_uploads_resp": "<b>Uploads:</b>{uploads}"})

        result = fmt_chats(chats, uploads, t)

        assert result is not None
        assert "<b>Uploads:</b>" in result
        assert "export.json" in result
        assert "2024-03-15" in result

    def test_skips_processed_uploads(self):
        """Test that already processed uploads are not shown."""
        chats: list[Chat] = []
        uploads: list[ChatUpload] = [
            ChatUpload(
                file_id=42,
                filename="export.json",
                file_path="/path/to/file",
                chat_type="telegram",
                created_at="2024-03-15T10:30:00",
                processed_at="2024-03-15T11:00:00",
            )
        ]
        t = self._make_t({})

        result = fmt_chats(chats, uploads, t)

        assert result is None


class TestListChatsHelper:
    """Unit tests for the _list_chats helper function."""

    def test_sends_no_chats_when_empty(self, mock_message, mock_bot_user, loop):
        """Test that 'no_chats' is sent when user has no chats or uploads."""
        mock_bot_user.chats = []
        mock_bot_user.uploads = []

        loop.run_until_complete(_list_chats(mock_bot_user, mock_message))

        mock_message.reply_text.assert_called_once_with("No groups available...")
        mock_message.reply_html.assert_not_called()

    def test_sends_html_when_has_chats(self, mock_message, mock_bot_user, loop):
        """Test that HTML is sent when user has chats."""
        loop.run_until_complete(_list_chats(mock_bot_user, mock_message))

        mock_message.reply_html.assert_called_once()
        mock_message.reply_text.assert_not_called()
        html = mock_message.reply_html.call_args[0][0]
        assert "Test Group" in html
        assert "Another Group" in html


class TestPendingQuestion:
    """Test that pending questions are asked after API key is set."""

    def test_question_stored_when_no_api_key(self, mock_update, mock_context, mock_bot_user, loop, monkeypatch):
        """When user asks a question without API key, it should be stored."""
        mock_bot_user.selected_chat_id = 1
        mock_bot_user.selected_model = "gemini-2.0-flash"
        mock_bot_user.selected_model_api_key = None
        mock_bot_user.pending_question = None
        mock_bot_user.state = UserState.NORMAL

        mock_load_user = AsyncMock(return_value=mock_bot_user)
        mock_ask = AsyncMock()
        monkeypatch.setattr(bot_module.User, "load_user", mock_load_user)
        monkeypatch.setattr(bot_module, "_ask_and_respond", mock_ask)

        mock_update.message.text = "What happened yesterday?"

        loop.run_until_complete(handle_message(mock_update, mock_context))

        # Question should be stored, not asked yet
        assert mock_bot_user.pending_question == "What happened yesterday?"
        mock_ask.assert_not_called()

    def test_pending_question_asked_after_api_key_set(
        self, mock_update, mock_context, mock_bot_user, loop, monkeypatch
    ):
        """After API key is set, pending question should be asked."""
        mock_bot_user.selected_chat_id = 1
        mock_bot_user.selected_model = "gemini-2.0-flash"
        mock_bot_user.selected_model_source = "gemini"
        mock_bot_user.api_keys = {}
        mock_bot_user.pending_question = "What happened yesterday?"
        mock_bot_user.state = UserState.AWAITING_API_KEY

        mock_load_user = AsyncMock(return_value=mock_bot_user)
        mock_ask = AsyncMock()
        monkeypatch.setattr(bot_module.User, "load_user", mock_load_user)
        monkeypatch.setattr(bot_module, "_ask_and_respond", mock_ask)

        # User sends the API key
        mock_update.message.text = "my-secret-api-key"

        loop.run_until_complete(handle_message(mock_update, mock_context))

        # API key saved confirmation should be sent
        mock_update.message.reply_text.assert_called()
        # Pending question should be asked
        mock_ask.assert_called_once_with(mock_bot_user, "What happened yesterday?", mock_update.message)
        # Pending question should be cleared
        assert mock_bot_user.pending_question is None

    def test_no_pending_question_after_api_key_set(self, mock_update, mock_context, mock_bot_user, loop, monkeypatch):
        """When API key is set without pending question, nothing extra happens."""
        mock_bot_user.selected_model = "gemini-2.0-flash"
        mock_bot_user.selected_model_source = "gemini"
        mock_bot_user.api_keys = {}
        mock_bot_user.pending_question = None
        mock_bot_user.state = UserState.AWAITING_API_KEY

        mock_load_user = AsyncMock(return_value=mock_bot_user)
        mock_ask = AsyncMock()
        monkeypatch.setattr(bot_module.User, "load_user", mock_load_user)
        monkeypatch.setattr(bot_module, "_ask_and_respond", mock_ask)

        mock_update.message.text = "my-secret-api-key"

        loop.run_until_complete(handle_message(mock_update, mock_context))

        # Only the API key saved message should be sent
        mock_update.message.reply_text.assert_called_once()
        # No question should be asked
        mock_ask.assert_not_called()
