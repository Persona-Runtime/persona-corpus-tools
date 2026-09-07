from __future__ import annotations

from pathlib import Path

from persona_ingestion.adapters.transcript_txt import parse_transcript_txt
from persona_ingestion.canonical.models import SourceMeta
from persona_ingestion.canonical.personas import PersonaConfig
from persona_ingestion.intake.hashing import sha256_file, sha256_text
from persona_ingestion.reporting.writer import write_parse_result

from ..conftest import TRANSCRIPT_FIXTURE


def test_output_is_byte_identical_across_runs(
    tmp_path: Path, meta: SourceMeta, personas: PersonaConfig
) -> None:
    text = TRANSCRIPT_FIXTURE.read_text(encoding="utf-8")
    hashes = []
    for run in ("a", "b"):
        result = parse_transcript_txt(text, meta, personas, "character_a")
        paths = write_parse_result(tmp_path / run, result, meta.source_id)
        hashes.append(tuple(sha256_file(p) for p in paths.values()))
    assert hashes[0] == hashes[1]


def test_report_contains_no_wall_clock_fields(meta: SourceMeta, personas: PersonaConfig) -> None:
    result = parse_transcript_txt("[00:05] Character A\nx\n", meta, personas, "character_a")
    assert result.report is not None
    keys = set(result.report.to_dict())
    assert not keys & {"created_at", "generated_at", "timestamp", "started_at"}


def test_jsonl_rows_end_with_newline(
    tmp_path: Path, meta: SourceMeta, personas: PersonaConfig
) -> None:
    text = TRANSCRIPT_FIXTURE.read_text(encoding="utf-8")
    result = parse_transcript_txt(text, meta, personas, "character_a")
    paths = write_parse_result(tmp_path, result, meta.source_id)
    content = paths["utterances"].read_text(encoding="utf-8")
    assert content.endswith("\n")
    assert len(content.splitlines()) == len(result.utterances)


def test_sha256_text_matches_file(tmp_path: Path) -> None:
    path = tmp_path / "x.txt"
    path.write_text("hello", encoding="utf-8")
    assert sha256_file(path) == sha256_text("hello")
