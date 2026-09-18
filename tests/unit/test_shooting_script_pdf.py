from __future__ import annotations

import pytest

from persona_corpus_tools.adapters.errors import AdapterParseError
from persona_corpus_tools.adapters.shooting_script_pdf import (
    PARSER_VERSION,
    parse_shooting_script_layout,
)
from persona_corpus_tools.canonical.models import SourceMeta
from persona_corpus_tools.canonical.personas import PersonaConfig


def _meta() -> SourceMeta:
    return SourceMeta(
        source_id="sherlock-s01e01",
        character_id="sherlock",
        series="sherlock",
        episode="S01E01",
        language="en",
        sha256="a" * 64,
        input_type="script_pdf",
        dataset_version="ds1-testfixture0000",
    )


def _personas() -> PersonaConfig:
    return PersonaConfig(
        alias_to_persona={"sherlock": "sherlock", "john": "john"},
        non_dialogue_keys=frozenset(),
    )


def test_extracts_target_dialogue_with_layout_locator_and_continuation() -> None:
    layout = """SCRIPT HEADER
                     SHERLOCK
           (quietly)
           First physical line.
           Second physical line.
                     JOHN
           Other speaker.
                     SHERLOCK (V.O.)
           Voice-over line.
                     (MORE)
\fSCRIPT HEADER PAGE TWO
                     SHERLOCK (CONT'D)
           Continued after the page break.
      Sherlock walks away.
                     SHERLOCK/JOHN
           Joint line.
"""

    result = parse_shooting_script_layout(layout, _meta(), _personas(), "sherlock")

    assert [utterance.text for utterance in result.utterances] == [
        "First physical line. Second physical line.",
        "Voice-over line. Continued after the page break.",
    ]
    first, second = result.utterances
    assert first.utterance_id == "sherlock-s01e01-p001-0001"
    assert first.source_locator == {"page": 1, "page_line": 2, "cue": "SHERLOCK"}
    assert second.source_locator == {
        "page": 1,
        "page_line": 8,
        "cue": "SHERLOCK (V.O.)",
    }
    assert all(utterance.speaker_normalized == "sherlock" for utterance in result.utterances)
    assert result.report is not None
    assert result.report.parser_version == PARSER_VERSION
    assert result.report.counts["delivery_directions_excluded"] == 1
    assert result.report.counts["excluded_joint_cue"] == 1
    assert not result.quarantine


def test_page_break_without_more_does_not_merge_dialogue() -> None:
    layout = """                     SHERLOCK
           First page only.
\fSCRIPT HEADER PAGE TWO
           This is not attributed without a new cue.
"""

    result = parse_shooting_script_layout(layout, _meta(), _personas(), "sherlock")

    assert [utterance.text for utterance in result.utterances] == ["First page only."]
    assert result.report is not None
    assert result.report.counts["pages"] == 2


def test_trailing_form_feed_does_not_add_a_phantom_page() -> None:
    layout = """                     SHERLOCK
           First page.
\f                     SHERLOCK
           Second page.
\f"""

    result = parse_shooting_script_layout(layout, _meta(), _personas(), "sherlock")

    assert [utterance.text for utterance in result.utterances] == ["First page.", "Second page."]
    assert result.report is not None
    assert result.report.counts["pages"] == 2


def test_quarantines_malformed_cue_without_merging_its_body() -> None:
    layout = """                     SHERLOCK
           Valid line.
                     SHERLOCK (quietly)
           This must not leak into the previous utterance.
                     SHERLOCK
           Final line.
"""

    result = parse_shooting_script_layout(layout, _meta(), _personas(), "sherlock")

    assert [utterance.text for utterance in result.utterances] == ["Valid line.", "Final line."]
    assert [(record.reason, record.raw) for record in result.quarantine] == [
        ("ambiguous_cue", "SHERLOCK (quietly)"),
    ]


def test_empty_target_dialogue_is_quarantined() -> None:
    layout = """                     SHERLOCK
                     JOHN
           Other line.
                     SHERLOCK
           Final line.
"""

    result = parse_shooting_script_layout(layout, _meta(), _personas(), "sherlock")

    assert [utterance.text for utterance in result.utterances] == ["Final line."]
    assert [record.reason for record in result.quarantine] == ["empty_dialogue"]


def test_rejects_layout_without_speaker_cues() -> None:
    with pytest.raises(AdapterParseError, match="no screenplay speaker cues"):
        parse_shooting_script_layout(
            "A scanned-looking page with no cues.", _meta(), _personas(), "sherlock"
        )
