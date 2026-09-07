from __future__ import annotations

from pathlib import Path

import pytest

from persona_ingestion.canonical.personas import (
    ConfigError,
    PersonaConfig,
    SpeakerKind,
    load_persona_config,
    strip_speaker_qualifier,
)


def test_resolves_alias_case_insensitively(personas: PersonaConfig) -> None:
    resolution = personas.resolve("char a")
    assert resolution.kind is SpeakerKind.PERSONA
    assert resolution.persona_id == "character_a"


def test_persona_id_itself_is_an_alias(personas: PersonaConfig) -> None:
    assert personas.resolve("character_a").persona_id == "character_a"


def test_non_dialogue_speaker(personas: PersonaConfig) -> None:
    assert personas.resolve("Sign").kind is SpeakerKind.NON_DIALOGUE


def test_unknown_speaker_is_not_guessed(personas: PersonaConfig) -> None:
    assert personas.resolve("Character C").kind is SpeakerKind.UNKNOWN


def test_trailing_qualifier_is_stripped(personas: PersonaConfig) -> None:
    assert personas.resolve("Character A (whispering)").persona_id == "character_a"
    assert personas.resolve("Character A [OS]").persona_id == "character_a"


def test_qualifier_stripping_never_empties_the_speaker() -> None:
    assert strip_speaker_qualifier("(laughing)") == "(laughing)"


def test_duplicate_alias_across_personas_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "personas.yaml"
    config.write_text(
        "personas:\n  - id: one\n    aliases: ['Shared']\n  - id: two\n    aliases: ['shared']\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="claimed by both"):
        load_persona_config(config)


def test_alias_colliding_with_non_dialogue_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "personas.yaml"
    config.write_text(
        "personas:\n  - id: one\n    aliases: ['Sign']\nnon_dialogue_speakers: ['sign']\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_persona_config(config)


def test_empty_personas_list_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "personas.yaml"
    config.write_text("personas: []\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_persona_config(config)
