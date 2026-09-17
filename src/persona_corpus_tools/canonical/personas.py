"""Persona alias configuration.

A speaker string in a transcript is resolved to exactly one of:
  - a persona id           (a character we may extract)
  - a non-dialogue marker  (sign, caption, translator note ... )
  - unknown                (recorded, never guessed)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from .normalize import normalize_text, speaker_key


class ConfigError(ValueError):
    """Raised when a persona configuration file is unusable."""


class SpeakerKind(StrEnum):
    PERSONA = "persona"
    NON_DIALOGUE = "non_dialogue"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SpeakerResolution:
    kind: SpeakerKind
    persona_id: str | None = None


@dataclass(frozen=True)
class PersonaConfig:
    """Immutable alias table built from ``configs/personas.yaml``."""

    alias_to_persona: dict[str, str]
    non_dialogue_keys: frozenset[str]

    @property
    def persona_ids(self) -> frozenset[str]:
        return frozenset(self.alias_to_persona.values())

    def resolve(self, speaker_raw: str) -> SpeakerResolution:
        key = speaker_key(strip_speaker_qualifier(speaker_raw))
        if not key:
            return SpeakerResolution(SpeakerKind.UNKNOWN)
        if key in self.non_dialogue_keys:
            return SpeakerResolution(SpeakerKind.NON_DIALOGUE)
        persona_id = self.alias_to_persona.get(key)
        if persona_id is not None:
            return SpeakerResolution(SpeakerKind.PERSONA, persona_id)
        return SpeakerResolution(SpeakerKind.UNKNOWN)


def strip_speaker_qualifier(speaker_raw: str) -> str:
    """Drop a trailing delivery note: ``Character A (whispering)`` -> ``Character A``.

    Only a trailing qualifier is removed, and only if something is left over.
    """
    text = normalize_text(speaker_raw)
    for open_ch, close_ch in (("(", ")"), ("[", "]")):
        if text.endswith(close_ch) and open_ch in text:
            head = text[: text.rindex(open_ch)].strip()
            if head:
                text = head
    return text


def load_persona_config(path: Path) -> PersonaConfig:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")

    personas = raw.get("personas")
    if not isinstance(personas, list) or not personas:
        raise ConfigError(f"{path}: 'personas' must be a non-empty list")

    alias_to_persona: dict[str, str] = {}
    alias_owner: dict[str, str] = {}

    for entry in personas:
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: each persona entry must be a mapping")
        persona_id = entry.get("id")
        if not isinstance(persona_id, str) or not persona_id.strip():
            raise ConfigError(f"{path}: persona entry is missing a string 'id'")
        aliases = entry.get("aliases") or []
        if not isinstance(aliases, list):
            raise ConfigError(f"{path}: '{persona_id}' aliases must be a list")

        for alias in [persona_id, *aliases]:
            if not isinstance(alias, str):
                raise ConfigError(f"{path}: '{persona_id}' has a non-string alias")
            key = speaker_key(alias)
            if not key:
                raise ConfigError(f"{path}: '{persona_id}' has an empty alias")
            owner = alias_owner.get(key)
            if owner is not None and owner != persona_id:
                raise ConfigError(
                    f"{path}: alias {alias!r} is claimed by both {owner!r} and {persona_id!r}"
                )
            alias_owner[key] = persona_id
            alias_to_persona[key] = persona_id

    non_dialogue_raw = raw.get("non_dialogue_speakers") or []
    if not isinstance(non_dialogue_raw, list):
        raise ConfigError(f"{path}: 'non_dialogue_speakers' must be a list")
    non_dialogue_keys = {speaker_key(str(item)) for item in non_dialogue_raw}
    non_dialogue_keys.discard("")

    collision = non_dialogue_keys & set(alias_to_persona)
    if collision:
        raise ConfigError(
            f"{path}: {sorted(collision)} used as both persona alias and non-dialogue"
        )

    return PersonaConfig(
        alias_to_persona=alias_to_persona,
        non_dialogue_keys=frozenset(non_dialogue_keys),
    )
