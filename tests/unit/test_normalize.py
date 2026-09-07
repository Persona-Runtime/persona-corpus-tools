from __future__ import annotations

from persona_ingestion.canonical.normalize import merge_lines, normalize_text, speaker_key


def test_normalize_collapses_whitespace_and_nbsp() -> None:
    assert normalize_text("  a  b\t c  ") == "a b c"  # noqa: RUF001 (the NBSP is the input under test)


def test_normalize_strips_zero_width_characters() -> None:
    assert normalize_text("a​b﻿") == "ab"


def test_normalize_applies_nfc() -> None:
    assert normalize_text("é") == "é"


def test_merge_lines_joins_with_single_space_and_drops_blanks() -> None:
    assert merge_lines(["one", "", "  two  ", "three"]) == "one two three"


def test_normalize_does_not_touch_punctuation_or_casing() -> None:
    assert normalize_text("Well?! FINE...") == "Well?! FINE..."


def test_speaker_key_is_case_and_space_insensitive() -> None:
    assert speaker_key("  CHARACTER   a ") == speaker_key("Character A")
