from __future__ import annotations

import pytest

from persona_ingestion.adapters.transcript_txt import (
    PARSER_VERSION,
    ParseError,
    parse_transcript_txt,
    timestamp_to_seconds,
)
from persona_ingestion.canonical.models import ParseResult, SourceMeta
from persona_ingestion.canonical.personas import PersonaConfig

from ..conftest import TRANSCRIPT_FIXTURE


def parse(
    text: str, meta: SourceMeta, personas: PersonaConfig, target: str = "character_a"
) -> ParseResult:
    return parse_transcript_txt(text, meta, personas, target)


# --- multiline merge -------------------------------------------------------


def test_multiline_block_is_merged_into_one_utterance(
    meta: SourceMeta, personas: PersonaConfig
) -> None:
    text = "[00:05] Character A\nfirst\nsecond\n\nthird\n"
    result = parse(text, meta, personas)
    assert len(result.utterances) == 1
    assert result.utterances[0].text == "first second third"


def test_block_ends_at_next_header(meta: SourceMeta, personas: PersonaConfig) -> None:
    text = "[00:05] Character A\none\n[00:09] Character A\ntwo\n"
    result = parse(text, meta, personas)
    assert [u.text for u in result.utterances] == ["one", "two"]


# --- alias mapping ---------------------------------------------------------


@pytest.mark.parametrize("speaker", ["Character A", "Char A", "A", "character a"])
def test_aliases_map_to_the_same_persona(
    speaker: str, meta: SourceMeta, personas: PersonaConfig
) -> None:
    result = parse(f"[00:05] {speaker}\nline\n", meta, personas)
    assert len(result.utterances) == 1
    assert result.utterances[0].speaker_normalized == "character_a"
    assert result.utterances[0].speaker_raw == speaker


def test_delivery_note_after_the_name_still_resolves(
    meta: SourceMeta, personas: PersonaConfig
) -> None:
    result = parse("[00:05] Character A (whispering)\nline\n", meta, personas)
    assert len(result.utterances) == 1
    assert result.utterances[0].speaker_raw == "Character A (whispering)"


# --- non-target exclusion --------------------------------------------------


def test_other_persona_is_excluded(meta: SourceMeta, personas: PersonaConfig) -> None:
    result = parse("[00:05] Character B\nline\n", meta, personas)
    assert result.utterances == []
    assert result.report is not None
    assert result.report.counts["excluded_other_persona"] == 1


def test_non_dialogue_speaker_is_excluded_not_quarantined(
    meta: SourceMeta, personas: PersonaConfig
) -> None:
    result = parse("[00:05] Sign\nCLOSED\n", meta, personas)
    assert result.utterances == []
    assert result.quarantine == []
    assert result.report is not None
    assert result.report.counts["excluded_non_dialogue"] == 1


def test_unknown_speaker_is_excluded_and_counted(meta: SourceMeta, personas: PersonaConfig) -> None:
    result = parse("[00:05] Character C\nline\n", meta, personas)
    assert result.utterances == []
    assert result.report is not None
    assert result.report.counts["excluded_unknown_speaker"] == 1
    assert result.report.speakers_seen["Character C"] == 1


def test_zero_leakage_for_every_non_target_speaker(
    meta: SourceMeta, personas: PersonaConfig
) -> None:
    text = TRANSCRIPT_FIXTURE.read_text(encoding="utf-8")
    result = parse(text, meta, personas)
    assert {u.speaker_normalized for u in result.utterances} == {"character_a"}
    assert all(u.character_id == "character_a" for u in result.utterances)


# --- quarantine ------------------------------------------------------------


def test_malformed_header_is_quarantined(meta: SourceMeta, personas: PersonaConfig) -> None:
    result = parse("[0:33 Character A\nbody\n", meta, personas)
    assert result.utterances == []
    assert [q.reason for q in result.quarantine] == [
        "malformed_header",
        "orphaned_body_after_malformed_header",
    ]
    assert result.quarantine[0].line_no == 1
    assert result.quarantine[0].parser_version == PARSER_VERSION


def test_malformed_header_does_not_leak_its_body_into_the_previous_speaker(
    meta: SourceMeta, personas: PersonaConfig
) -> None:
    """Regression: orphaned body lines must never be attributed to the block above."""
    text = "[00:05] Character A\nkept\n[0:33 Character A\norphan line\n"
    result = parse(text, meta, personas)
    assert [u.text for u in result.utterances] == ["kept"]
    assert [q.reason for q in result.quarantine] == [
        "malformed_header",
        "orphaned_body_after_malformed_header",
    ]


def test_orphan_state_clears_at_the_next_valid_header(
    meta: SourceMeta, personas: PersonaConfig
) -> None:
    text = "[0:33 Character A\norphan\n[00:40] Character A\nclean\n"
    result = parse(text, meta, personas)
    assert [u.text for u in result.utterances] == ["clean"]


def test_text_before_first_header_is_quarantined(meta: SourceMeta, personas: PersonaConfig) -> None:
    result = parse("preamble line\n\n[00:05] Character A\nline\n", meta, personas)
    assert [q.reason for q in result.quarantine] == ["text_before_first_header"]
    assert len(result.utterances) == 1


def test_header_without_body_is_quarantined(meta: SourceMeta, personas: PersonaConfig) -> None:
    result = parse("[00:05] Character A\n\n[00:09] Character A\nline\n", meta, personas)
    assert [q.reason for q in result.quarantine] == ["empty_body"]
    assert len(result.utterances) == 1


def test_nothing_is_silently_dropped(meta: SourceMeta, personas: PersonaConfig) -> None:
    """Every input line is either merged into a block, or quarantined, or blank."""
    text = TRANSCRIPT_FIXTURE.read_text(encoding="utf-8")
    result = parse(text, meta, personas)
    assert result.report is not None
    assert result.report.counts["input_lines"] == len(text.splitlines())
    blocks = result.report.counts["blocks_total"]
    accounted = (
        result.report.counts["target"]
        + result.report.counts["excluded_other_persona"]
        + result.report.counts["excluded_non_dialogue"]
        + result.report.counts["excluded_unknown_speaker"]
        + sum(1 for q in result.quarantine if q.reason == "empty_body")
    )
    assert accounted == blocks


# --- body text that merely looks like markup -------------------------------


def test_bracketed_body_line_is_not_treated_as_a_header(
    meta: SourceMeta, personas: PersonaConfig
) -> None:
    result = parse("[00:05] Character A\n[NOTICE] still body\n", meta, personas)
    assert result.utterances[0].text == "[NOTICE] still body"
    assert result.quarantine == []


# --- locators and ids ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "seconds"), [("00:06", 6), ("01:30", 90), ("01:02:07", 3727), ("100:00", 6000)]
)
def test_timestamp_to_seconds(value: str, seconds: int) -> None:
    assert timestamp_to_seconds(value) == seconds


def test_locator_carries_timestamp_and_line(meta: SourceMeta, personas: PersonaConfig) -> None:
    result = parse("\n[01:02:07] Character A\nline\n", meta, personas)
    locator = result.utterances[0].source_locator
    assert locator == {"timestamp": "01:02:07", "timestamp_seconds": 3727, "line": 2}


def test_every_utterance_has_a_locator(meta: SourceMeta, personas: PersonaConfig) -> None:
    text = TRANSCRIPT_FIXTURE.read_text(encoding="utf-8")
    result = parse(text, meta, personas)
    assert result.utterances
    for utterance in result.utterances:
        assert utterance.source_locator["timestamp"]
        assert utterance.source_locator["line"] > 0


def test_utterance_ids_are_unique_and_stable_across_targets(
    meta: SourceMeta, personas: PersonaConfig
) -> None:
    text = "[00:05] Character A\nx\n[00:09] Character B\ny\n[00:12] Character A\nz\n"
    a = parse(text, meta, personas, "character_a")
    b = parse(text, meta, personas, "character_b")
    ids = [u.utterance_id for u in a.utterances]
    assert ids == sorted(set(ids))
    # block sequence is global, so B's utterance keeps sequence 0002
    assert b.utterances[0].utterance_id.endswith("-0002")


# --- guards ----------------------------------------------------------------


def test_unknown_target_persona_is_rejected(meta: SourceMeta, personas: PersonaConfig) -> None:
    with pytest.raises(ParseError):
        parse_transcript_txt("[00:05] Character A\nx\n", meta, personas, "nope")


def test_empty_input_produces_empty_result(meta: SourceMeta, personas: PersonaConfig) -> None:
    result = parse("", meta, personas)
    assert result.utterances == []
    assert result.quarantine == []
