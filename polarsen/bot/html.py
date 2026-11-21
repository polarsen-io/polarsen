import html
import re

__all__ = ("escape_html", "sanitize_html")

# Tags the bot is willing to let through when the text comes from a model.
# Keep this list small: everything else is rendered as literal text.
_ALLOWED_TAGS = frozenset({"b", "strong", "i", "em", "u", "s", "code", "pre", "blockquote"})
_TAG_RE = re.compile(r"</?([a-z]+)>")

# Entities the model may already have escaped. They pass through untouched so
# text such as "<b>Tom &amp; Jerry</b>" keeps rendering as "Tom & Jerry".
_ENTITY_RE = re.compile(r"&(?:amp|lt|gt|quot|#[0-9]+|#[xX][0-9a-fA-F]+);")


def escape_html(value: object) -> str:
    """Escape a value so Telegram renders it as text instead of HTML."""
    return html.escape(str(value), quote=False)


def sanitize_html(text: str) -> str:
    """Escape all HTML except properly balanced tags from a small allowlist.

    Model answers may contain intentional formatting such as <b> or <i>. Every
    other tag, including an unclosed allowed tag, is escaped so attacker
    controlled text is shown literally. Valid entities are kept as they are so
    already-escaped text is not escaped twice.
    """
    tokens = _tokenize(text)
    matched = _matching_tag_indexes(tokens)
    return "".join(
        token if is_tag and index in matched else _escape_text(token)
        for index, (is_tag, token) in enumerate(tokens)
    )


def _escape_text(text: str) -> str:
    """Escape text, leaving valid HTML entities and their ampersands intact."""
    parts: list[str] = []
    position = 0
    for match in _ENTITY_RE.finditer(text):
        parts.append(escape_html(text[position : match.start()]))
        parts.append(match.group(0))
        position = match.end()
    parts.append(escape_html(text[position:]))
    return "".join(parts)


def _tokenize(text: str) -> list[tuple[bool, str]]:
    tokens: list[tuple[bool, str]] = []
    position = 0
    while position < len(text):
        start = text.find("<", position)
        if start == -1:
            tokens.append((False, text[position:]))
            break
        if start > position:
            tokens.append((False, text[position:start]))
        end = text.find(">", start + 1)
        if end == -1:
            tokens.append((False, text[start:]))
            break
        token = text[start : end + 1]
        match = _TAG_RE.fullmatch(token)
        tokens.append((match is not None and match.group(1) in _ALLOWED_TAGS, token))
        position = end + 1
    return tokens


def _matching_tag_indexes(tokens: list[tuple[bool, str]]) -> set[int]:
    stack: list[tuple[str, int]] = []
    matched: set[int] = set()
    for index, (is_tag, token) in enumerate(tokens):
        if not is_tag:
            continue
        if token.startswith("</"):
            name = token[2:-1]
            if stack and stack[-1][0] == name:
                _, open_index = stack.pop()
                matched.add(open_index)
                matched.add(index)
        else:
            stack.append((token[1:-1], index))
    return matched
