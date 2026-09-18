from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from persona_corpus_tools.intake.manifest import (
    OUTPUT_AFFECTING_FIELDS,
    IntegrityError,
    ManifestError,
    RetentionError,
    check_retention,
    load_manifest,
    load_manifests,
    read_verified_bytes,
)

from ..conftest import MANIFEST_FIXTURE, PRIVATE_ROOT

VALID = """
source_id: s1
character_id: character_a
series: sample
episode: "001"
input_type: transcript_txt
language: en
rights_status: synthetic
file: transcript_txt/sample_series_ep001.txt
sha256: "{sha}"
parser_profile: transcript-txt
"""


def write(tmp_path: Path, body: str, name: str = "m.yaml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_the_fixture_manifest() -> None:
    m = load_manifest(MANIFEST_FIXTURE)
    assert m.source_id == "sample-series-ep001"
    assert m.character_id == "character_a"
    assert m.parser_profile == "transcript-txt"
    assert m.delete_at == date(2099, 12, 31)


def test_verified_read_returns_the_bytes(tmp_path: Path) -> None:
    m = load_manifest(MANIFEST_FIXTURE)
    data = read_verified_bytes(m, m.resolve_file(PRIVATE_ROOT))
    assert data.startswith(b"Sample Series")


# --- schema gate -----------------------------------------------------------


@pytest.mark.parametrize("field", ["source_id", "character_id", "series", "sha256", "file"])
def test_missing_required_field_is_rejected(tmp_path: Path, field: str) -> None:
    body = "\n".join(
        line for line in VALID.format(sha="a" * 64).splitlines() if not line.startswith(f"{field}:")
    )
    with pytest.raises(ManifestError):
        load_manifest(write(tmp_path, body))


def test_unknown_input_type_is_rejected(tmp_path: Path) -> None:
    body = VALID.format(sha="a" * 64).replace("input_type: transcript_txt", "input_type: srt")
    with pytest.raises(ManifestError, match="input_type"):
        load_manifest(write(tmp_path, body))


def test_unknown_rights_status_is_rejected(tmp_path: Path) -> None:
    """rights_status is not enforced, but a typo in it is still a mistake."""
    body = VALID.format(sha="a" * 64).replace("rights_status: synthetic", "rights_status: yolo")
    with pytest.raises(ManifestError, match="rights_status"):
        load_manifest(write(tmp_path, body))


def test_private_unverified_is_allowed_through(tmp_path: Path) -> None:
    """Rights are recorded, not enforced — this must load without complaint."""
    body = VALID.format(sha="a" * 64).replace(
        "rights_status: synthetic", "rights_status: private_unverified"
    )
    assert load_manifest(write(tmp_path, body)).rights_status == "private_unverified"


@pytest.mark.parametrize("bad", ["abc", "z" * 64, "a" * 63])
def test_malformed_sha256_is_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ManifestError, match="sha256"):
        load_manifest(write(tmp_path, VALID.format(sha=bad)))


# --- dates: both YAML spellings, no timestamps -----------------------------


def test_unquoted_yaml_date_is_accepted(tmp_path: Path) -> None:
    body = VALID.format(sha="a" * 64) + "\ndelete_at: 2027-08-30\n"
    assert load_manifest(write(tmp_path, body)).delete_at == date(2027, 8, 30)


def test_quoted_iso_string_is_accepted(tmp_path: Path) -> None:
    """A quoted date is a normal way to write YAML and must not be rejected."""
    body = VALID.format(sha="a" * 64) + '\ndelete_at: "2027-08-30"\n'
    assert load_manifest(write(tmp_path, body)).delete_at == date(2027, 8, 30)


def test_timestamp_is_rejected_explicitly(tmp_path: Path) -> None:
    """datetime subclasses date, so it would pass an isinstance check and then
    blow up later when compared against a date."""
    body = VALID.format(sha="a" * 64) + "\ndelete_at: 2027-08-30 12:00:00\n"
    with pytest.raises(ManifestError, match="not a timestamp"):
        load_manifest(write(tmp_path, body))


def test_unparseable_date_string_is_rejected(tmp_path: Path) -> None:
    body = VALID.format(sha="a" * 64) + '\ndelete_at: "not a date"\n'
    with pytest.raises(ManifestError, match="ISO date"):
        load_manifest(write(tmp_path, body))


# --- path containment ------------------------------------------------------


def test_file_cannot_escape_the_private_root(tmp_path: Path) -> None:
    body = VALID.format(sha="a" * 64).replace(
        "file: transcript_txt/sample_series_ep001.txt", "file: ../../../etc/passwd"
    )
    m = load_manifest(write(tmp_path, body))
    with pytest.raises(ManifestError, match="escapes"):
        m.resolve_file(tmp_path)


# --- integrity gate --------------------------------------------------------


def test_hash_mismatch_is_fatal(tmp_path: Path) -> None:
    data = tmp_path / "data.txt"
    data.write_text("hello", encoding="utf-8")
    m = load_manifest(write(tmp_path, VALID.format(sha="a" * 64)))
    with pytest.raises(IntegrityError, match="mismatch"):
        read_verified_bytes(m, data)


def test_missing_file_is_an_integrity_error(tmp_path: Path) -> None:
    m = load_manifest(write(tmp_path, VALID.format(sha="a" * 64)))
    with pytest.raises(IntegrityError, match="not found"):
        read_verified_bytes(m, tmp_path / "nope.txt")


# --- retention gate --------------------------------------------------------


def test_retention_expiry_blocks_processing(tmp_path: Path) -> None:
    body = VALID.format(sha="a" * 64) + "\ndelete_at: 2020-01-01\n"
    with pytest.raises(RetentionError, match="retention expired"):
        check_retention(load_manifest(write(tmp_path, body)), date(2026, 9, 7))


def test_retention_on_the_final_day_is_still_allowed(tmp_path: Path) -> None:
    body = VALID.format(sha="a" * 64) + "\ndelete_at: 2026-09-07\n"
    check_retention(load_manifest(write(tmp_path, body)), date(2026, 9, 7))


def test_absent_delete_at_never_expires(tmp_path: Path) -> None:
    check_retention(load_manifest(write(tmp_path, VALID.format(sha="a" * 64))), date(2099, 1, 1))


# --- output-affecting field set --------------------------------------------


def test_output_affecting_covers_every_field_that_reaches_a_record() -> None:
    """If a field lands in an output record it must be in this tuple, or
    dataset_version will not notice when it changes."""
    assert set(OUTPUT_AFFECTING_FIELDS) >= {
        "character_id",
        "series",
        "episode",
        "language",
        "sha256",
    }


def test_output_affecting_snapshot_is_stringified() -> None:
    fields = load_manifest(MANIFEST_FIXTURE).output_affecting()
    assert fields["episode"] == "001"
    assert all(isinstance(v, str) for v in fields.values())


# --- batch loading ---------------------------------------------------------


def test_directory_load_is_sorted_and_rejects_duplicate_ids(tmp_path: Path) -> None:
    write(tmp_path, VALID.format(sha="a" * 64).replace("source_id: s1", "source_id: s2"), "b.yaml")
    write(tmp_path, VALID.format(sha="a" * 64), "a.yaml")
    assert [m.source_id for m in load_manifests(tmp_path)] == ["s1", "s2"]

    write(tmp_path, VALID.format(sha="a" * 64), "c.yaml")
    with pytest.raises(ManifestError, match="duplicate"):
        load_manifests(tmp_path)
