import asyncio
from types import SimpleNamespace

from polarsen.bot.bot import _fmt_summaries, fmt_chats
from polarsen.bot.html import escape_html, sanitize_html
from polarsen.bot.models import Chat, ChatUpload, EmbeddingResult

_TRANSLATIONS = {
    "last_message": "last message",
    "list_chats_resp": "{chats}",
    "list_uploads_resp": "{uploads}",
}


def _t(key: str, *args, **kwargs) -> str:
    template = _TRANSLATIONS.get(key, key)
    if args or kwargs:
        return template.format(*args, **kwargs)
    return template


def test_fmt_chats_escapes_chat_name():
    chats: list[Chat] = [{"id": 1, "name": "<script>alert(1)</script>", "cutoff_date": "2026-01-01"}]
    msg = fmt_chats(chats=chats, uploads=[], t=_t)

    assert msg is not None
    assert "<script>" not in msg
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in msg


def test_fmt_chats_escapes_unclosed_tag():
    chats: list[Chat] = [{"id": 1, "name": "<i>", "cutoff_date": "2026-01-01"}]
    msg = fmt_chats(chats=chats, uploads=[], t=_t)

    assert msg is not None
    assert "- 1 <i>&lt;i&gt;</i>" in msg


def test_fmt_chats_escapes_filename():
    uploads: list[ChatUpload] = [
        {
            "file_id": 1,
            "filename": "<img src=x onerror=alert(1)>.json",
            "file_path": "1/file.json",
            "created_at": "2026-01-01T00:00:00",
            "chat_type": "telegram",
            "processed_at": None,
        }
    ]
    msg = fmt_chats(chats=[], uploads=uploads, t=_t)

    assert msg is not None
    assert "<img" not in msg
    assert "&lt;img src=x onerror=alert(1)&gt;.json" in msg


def test_fmt_summaries_escapes_title_and_summary():
    summaries: list[EmbeddingResult] = [
        {"title": "<script>alert(1)</script>", "summary": "<i>hi", "day": "2026-01-01", "distance": 0.1}
    ]
    msg = _fmt_summaries(summaries)

    assert "<script>" not in msg
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in msg
    assert "&lt;i&gt;hi" in msg


def test_escape_html_leaves_no_live_tags():
    assert escape_html("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"


def test_sanitize_html_keeps_intentional_bold_and_italic():
    assert sanitize_html("<b>bold</b> and <i>italic</i>") == "<b>bold</b> and <i>italic</i>"


def test_sanitize_html_escapes_script_and_unclosed_tags():
    assert sanitize_html("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert sanitize_html("quoted <i>") == "quoted &lt;i&gt;"


def test_sanitize_html_escapes_text_inside_allowed_tags():
    assert sanitize_html("<b><script>x</script></b>") == "<b>&lt;script&gt;x&lt;/script&gt;</b>"


def test_sanitize_html_keeps_valid_entities():
    assert sanitize_html("<b>Tom &amp; Jerry</b>") == "<b>Tom &amp; Jerry</b>"
    assert sanitize_html("&lt;script&gt;") == "&lt;script&gt;"
    assert sanitize_html("&quot;quoted&quot; and &#39;apostrophe&#39;") == (
        "&quot;quoted&quot; and &#39;apostrophe&#39;"
    )
    assert sanitize_html("&#123; and &#x1F;") == "&#123; and &#x1F;"


def test_sanitize_html_escapes_raw_ampersands_and_unknown_entities():
    assert sanitize_html("Tom & Jerry") == "Tom &amp; Jerry"
    assert sanitize_html("&nbsp;") == "&amp;nbsp;"
    assert sanitize_html("<i>A &amp; B & C</i>") == "<i>A &amp; B &amp; C</i>"


def test_handle_message_sanitizes_model_answer(monkeypatch):
    from polarsen.bot import bot as bot_module

    user = bot_module.User(telegram_id=1)
    user.id = 1
    user.selected_chat_id = 1
    user.selected_model = "mistral-small-latest"
    user.api_keys = {"mistral": "key"}

    async def fake_load_user(_):
        return user

    async def fake_ask_question(**_):
        return {"response": "<b>ok</b><script>alert(1)</script>", "question_id": 1, "results": []}

    monkeypatch.setattr(bot_module.User, "load_user", fake_load_user)
    monkeypatch.setattr(bot_module, "ask_question", fake_ask_question)

    sent = {}

    class Message:
        text = "question"

        async def reply_html(self, text, reply_markup=None):
            sent["html"] = text

    update = SimpleNamespace(effective_user=SimpleNamespace(id=1), message=Message())

    asyncio.run(bot_module.handle_message(update, None))  # type: ignore[arg-type]

    assert sent["html"] == "<b>ok</b>&lt;script&gt;alert(1)&lt;/script&gt;"
