"""Minimal text normalization.

The rule is: make the text machine-comparable, never make it "nicer".
No rewording, no punctuation fixing, no profanity filtering.
"""

from __future__ import annotations

import re
import unicodedata

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """NFC-normalize, drop zero-width characters, collapse whitespace."""
    text = unicodedata.normalize("NFC", value)
    text = text.translate(_ZERO_WIDTH)
    text = text.replace(" ", " ")  # noqa: RUF001 (NBSP -> plain space is intended)
    return _WHITESPACE_RE.sub(" ", text).strip()


def merge_lines(lines: list[str]) -> str:
    """Merge the physical lines of one utterance into a single string."""
    parts = [normalize_text(line) for line in lines]
    return " ".join(part for part in parts if part)


def speaker_key(value: str) -> str:
    """Case- and whitespace-insensitive lookup key for speaker matching."""
    return normalize_text(value).casefold()
