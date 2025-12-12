import contextlib
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ("i18n", "I18n", "TranslateFn")

from typing import Callable, Generator, Any

_cur_dir = Path(os.path.dirname(__file__))
LANG_FILE = _cur_dir / "lang.toml"
COMMANDS_FILE = _cur_dir / "commands.toml"

TranslateFn = Callable[[str, *tuple[Any, ...]], str]


@dataclass
class I18n:
    _lang_data: dict[str, dict[str, str]]
    _commands_data: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls):
        """Load translations from lang.toml and commands.toml files."""
        return cls(
            tomllib.loads(LANG_FILE.read_text()),
            tomllib.loads(COMMANDS_FILE.read_text()),
        )

    @property
    def languages(self) -> list[str]:
        """Return list of available languages."""
        return list(self._lang_data.keys())

    def get(self, lang: str, key: str, *args, **kwargs) -> str:
        """
        Get the translation for the given key.
        If the key does not exist, it returns the key itself.
        """
        _translations = self._lang_data.get(lang)
        if _translations is None:
            raise ValueError(f"Language {lang:!r} not found in translations.")

        _translation = _translations.get(key) or ""
        if args or kwargs:
            return _translation.format(*args, **kwargs)
        return _translation

    def get_commands(self, lang: str) -> dict[str, str]:
        """
        Get command descriptions for a specific language.
        Returns a dict of {command_name: description}.
        """
        commands = self._commands_data.get(lang)
        if commands is None:
            raise ValueError(f"Language {lang!r} not found in command translations.")
        return commands

    @contextlib.contextmanager
    def set_lang(self, lang: str) -> Generator[TranslateFn, None, None]:
        def t(*args, **kwargs) -> str:
            """
            Translation function that uses the current language.
            """
            return self.get(lang, *args, **kwargs)

        yield t


i18n = I18n.load()
