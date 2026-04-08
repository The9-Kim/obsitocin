"""Pluggable tokenizer for FTS5 BM25 search indexing."""

from __future__ import annotations

import os
import re
from typing import Protocol, runtime_checkable


@runtime_checkable
class Tokenizer(Protocol):
    """Tokenizer protocol."""

    def tokenize(self, text: str) -> list[str]: ...

    @property
    def name(self) -> str: ...


class UnicodeTokenizer:
    """Zero-dependency fallback: Unicode-aware word splitting."""

    name = "unicode"

    def tokenize(self, text: str) -> list[str]:
        # Split on whitespace + punctuation, keep Korean/CJK and Latin tokens
        # Use regex: [\w] plus Korean Jamo + Syllable ranges
        tokens = re.findall(r"[\w\u3131-\u3163\uac00-\ud7a3]+", text.lower())
        return [t for t in tokens if len(t) >= 2]  # drop single chars


class KiwiTokenizer:
    """kiwipiepy morphological analyzer — keeps content morphemes."""

    name = "kiwi"

    # POS tags to keep: nouns (NNG, NNP, NNB), verbs (VV, VA), foreign (SL)
    KEEP_TAGS = frozenset({"NNG", "NNP", "NNB", "VV", "VA", "SL"})

    def __init__(self) -> None:
        from kiwipiepy import Kiwi
        self._kiwi = Kiwi()

    def tokenize(self, text: str) -> list[str]:
        tokens = []
        for token in self._kiwi.tokenize(text):
            if token.tag in self.KEEP_TAGS and len(token.form) >= 2:
                tokens.append(token.form.lower())
        return tokens


def get_tokenizer(name: str | None = None) -> Tokenizer:
    """Factory: returns tokenizer by name or OBS_TOKENIZER env var.

    Falls back to UnicodeTokenizer if kiwipiepy is not installed.
    """
    choice = (name or os.environ.get("OBS_TOKENIZER", "unicode")).strip().lower()
    if choice == "kiwi":
        try:
            return KiwiTokenizer()
        except ImportError:
            import warnings
            warnings.warn(
                "kiwipiepy not installed. Falling back to unicode tokenizer. "
                "Install with: pip install obsitocin[korean]"
            )
            return UnicodeTokenizer()
    return UnicodeTokenizer()
