"""Canonical data model shared by every input adapter."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# v2: records carry dataset_version; quarantine carries the same provenance
#     metadata as utterances (Loop 2).
SCHEMA_VERSION = "2"


@dataclass(frozen=True)
class SourceMeta:
    """Identity of one raw input file, as declared by its manifest.

    Loop 1 built this from CLI arguments; Loop 2 builds it from
    ``source_manifest.yaml``. ``series`` and ``episode`` are always declared
    values, never guessed from the file name.
    """

    source_id: str
    character_id: str
    series: str
    episode: str
    language: str
    sha256: str
    input_type: str
    dataset_version: str


@dataclass(frozen=True)
class Utterance:
    """One line of dialogue attributed to the target persona."""

    utterance_id: str
    character_id: str
    series: str
    episode: str
    source_id: str
    source_locator: dict[str, Any]
    speaker_raw: str
    speaker_normalized: str
    language: str
    text: str
    parser_version: str
    source_sha256: str
    dataset_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "utterance_id": self.utterance_id,
            "character_id": self.character_id,
            "series": self.series,
            "episode": self.episode,
            "source_id": self.source_id,
            "source_locator": self.source_locator,
            "speaker_raw": self.speaker_raw,
            "speaker_normalized": self.speaker_normalized,
            "language": self.language,
            "text": self.text,
            "parser_version": self.parser_version,
            "source_sha256": self.source_sha256,
            "dataset_version": self.dataset_version,
        }


@dataclass(frozen=True)
class QuarantineRecord:
    """A line the parser refused to interpret. Never silently dropped.

    ``raw`` can hold source text, so this file is source data like any other.
    It therefore carries the same provenance fields as an utterance: a
    quarantine file copied somewhere on its own must still say where it came
    from and which run produced it.
    """

    source_id: str
    line_no: int
    reason: str
    raw: str
    parser_version: str
    source_sha256: str
    dataset_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_id": self.source_id,
            "line_no": self.line_no,
            "reason": self.reason,
            "raw": self.raw,
            "parser_version": self.parser_version,
            "source_sha256": self.source_sha256,
            "dataset_version": self.dataset_version,
        }


@dataclass
class ParseReport:
    """Per-source counters.

    Deliberately free of wall-clock time so that re-running the same input
    reproduces an identical report hash (Loop 2 gate).
    """

    source_id: str
    character_id: str
    parser_version: str
    counts: Counter[str] = field(default_factory=Counter)
    speakers_seen: Counter[str] = field(default_factory=Counter)

    def bump(self, key: str, amount: int = 1) -> None:
        self.counts[key] += amount

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_id": self.source_id,
            "character_id": self.character_id,
            "parser_version": self.parser_version,
            "counts": dict(sorted(self.counts.items())),
            "speakers_seen": dict(sorted(self.speakers_seen.items())),
        }


@dataclass
class ParseResult:
    """Everything one adapter run produces."""

    utterances: list[Utterance] = field(default_factory=list)
    quarantine: list[QuarantineRecord] = field(default_factory=list)
    report: ParseReport | None = None


@dataclass(frozen=True)
class RunReport:
    """What one ingestion run produced, and from what.

    This is the audit record: it answers "where did this corpus come from"
    without needing the raw files, and it makes ``dataset_version`` checkable
    rather than merely asserted.

    Contains no wall-clock time — the same inputs must produce the same bytes.
    """

    dataset_version: str
    sources: list[dict[str, Any]]
    totals: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "dataset_version": self.dataset_version,
            "sources": self.sources,
            "totals": dict(sorted(self.totals.items())),
        }
