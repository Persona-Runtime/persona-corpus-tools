from __future__ import annotations

from pathlib import Path

import pytest

from persona_ingestion.canonical.versioning import (
    compute_corpus_version,
    compute_dataset_version,
    manifest_digest,
)
from persona_ingestion.intake.manifest import OUTPUT_AFFECTING_FIELDS, load_manifest

from ..conftest import MANIFEST_FIXTURE

BASE = {
    "parser_version": "transcript-txt-v1",
    "persona_config_digest": "a" * 64,
    "manifest_digest": "b" * 64,
}


def test_is_deterministic() -> None:
    assert compute_dataset_version(**BASE) == compute_dataset_version(**BASE)


@pytest.mark.parametrize("field", list(BASE))
def test_every_component_changes_the_version(field: str) -> None:
    other = {**BASE, field: "changed"}
    assert compute_dataset_version(**BASE) != compute_dataset_version(**other)


def test_components_cannot_be_confused_with_each_other() -> None:
    """A naive concatenation would let ('ab','c') and ('a','bc') collide."""
    a = compute_dataset_version(parser_version="ab", persona_config_digest="c", manifest_digest="d")
    b = compute_dataset_version(parser_version="a", persona_config_digest="bc", manifest_digest="d")
    assert a != b


# --- the gap the review found ----------------------------------------------

#: A valid replacement for each output-affecting field. Values must still pass
#: the manifest schema, so they are real alternatives rather than placeholders.
FIELD_MUTATIONS = {
    "character_id": ("character_id: character_a", "character_id: character_b"),
    "series": ("series: sample-series", "series: other-series"),
    "episode": ('episode: "001"', 'episode: "002"'),
    "language": ("language: en", "language: ko"),
    "input_type": ("input_type: transcript_txt", "input_type: script_pdf"),
    "parser_profile": ("parser_profile: transcript-txt", "parser_profile: script-pdf"),
    "sha256": (MANIFEST_FIXTURE.read_text("utf-8").split('sha256: "')[1][:64], "c" * 64),
}


def _version_of(manifest_path: Path) -> str:
    return compute_dataset_version(
        parser_version="p",
        persona_config_digest="c",
        manifest_digest=manifest_digest(load_manifest(manifest_path).output_affecting()),
    )


def test_every_output_affecting_field_has_a_mutation_case() -> None:
    """Adding a field to OUTPUT_AFFECTING_FIELDS must add a case below,
    otherwise the new field silently goes untested."""
    assert set(FIELD_MUTATIONS) == set(OUTPUT_AFFECTING_FIELDS)


@pytest.mark.parametrize("field", sorted(FIELD_MUTATIONS))
def test_changing_any_output_affecting_manifest_field_changes_the_version(
    tmp_path: Path, field: str
) -> None:
    """Same raw file, different manifest field, different output records.

    The version must notice. Before this was fixed, editing `episode` produced
    different records under an identical dataset_version.
    """
    old, new = FIELD_MUTATIONS[field]
    body = MANIFEST_FIXTURE.read_text(encoding="utf-8")
    assert old in body, f"mutation target not found for {field}: {old!r}"
    mutated = tmp_path / "mutated.yaml"
    mutated.write_text(body.replace(old, new), encoding="utf-8")

    assert _version_of(MANIFEST_FIXTURE) != _version_of(mutated), (
        f"{field} does not affect dataset_version"
    )


def test_manifest_digest_is_order_independent() -> None:
    assert manifest_digest({"a": "1", "b": "2"}) == manifest_digest({"b": "2", "a": "1"})


# --- corpus version ---------------------------------------------------------


def test_corpus_version_is_order_independent_but_content_sensitive() -> None:
    one = compute_corpus_version({"s1": "v1", "s2": "v2"})
    assert one == compute_corpus_version({"s2": "v2", "s1": "v1"})
    assert one != compute_corpus_version({"s1": "v1"})
    assert one != compute_corpus_version({"s1": "v1", "s2": "v3"})
