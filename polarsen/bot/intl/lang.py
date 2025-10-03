import contextlib
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = ("i18n", "I18n", "TranslateFn")

from typing import Callable, Generator, Any

_cur_dir = Path(os.path.dirname(__file__))
LANG_FILE = _cur_dir / "lang.toml"

TranslateFn = Callable[[str, *tuple[Any, ...]], str]


@dataclass
class I18n:
    _lang_data: dict[str, dict[str, str]]

    @classmethod
    def load(cls):
        """
        Load translations from the lang.toml file.
        """
        return cls(tomllib.loads(LANG_FILE.read_text()))

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

    @contextlib.contextmanager
    def set_lang(self, lang: str) -> Generator[TranslateFn, None, None]:
        def t(*args, **kwargs) -> str:
            """
            Translation function that uses the current language.
            """
            return self.get(lang, *args, **kwargs)

        yield t


i18n = I18n.load()
