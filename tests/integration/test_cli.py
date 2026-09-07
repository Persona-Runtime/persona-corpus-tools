from __future__ import annotations

import json
from pathlib import Path

from persona_ingestion.cli.main import main

from ..conftest import EXPECTED_DIR, REPO_ROOT, TRANSCRIPT_FIXTURE

GOLDEN = EXPECTED_DIR / "sample_series_ep001.utterances.jsonl"


def run_cli(out: Path) -> int:
    return main(
        [
            "--input",
            str(TRANSCRIPT_FIXTURE),
            "--personas",
            str(REPO_ROOT / "configs" / "personas.yaml"),
            "--character",
            "character_a",
            "--source-id",
            "sample-series-ep001",
            "--series",
            "sample-series",
            "--episode",
            "001",
            "--out",
            str(out),
        ]
    )


def test_cli_matches_golden_output(tmp_path: Path) -> None:
    assert run_cli(tmp_path) == 0
    produced = (tmp_path / "parsed" / "sample-series-ep001.jsonl").read_text(encoding="utf-8")
    assert produced == GOLDEN.read_text(encoding="utf-8")


def test_cli_records_the_real_source_hash(tmp_path: Path) -> None:
    run_cli(tmp_path)
    rows = [
        json.loads(line)
        for line in (tmp_path / "parsed" / "sample-series-ep001.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert rows
    digests = {row["source_sha256"] for row in rows}
    assert len(digests) == 1
    assert len(digests.pop()) == 64


def test_cli_writes_quarantine_and_report(tmp_path: Path) -> None:
    run_cli(tmp_path)
    quarantine = (tmp_path / "quarantine" / "sample-series-ep001.jsonl").read_text("utf-8")
    reasons = {json.loads(line)["reason"] for line in quarantine.splitlines()}
    assert reasons == {
        "text_before_first_header",
        "malformed_header",
        "orphaned_body_after_malformed_header",
        "empty_body",
    }

    report = json.loads((tmp_path / "reports" / "sample-series-ep001.json").read_text("utf-8"))
    assert report["character_id"] == "character_a"
    assert report["counts"]["target"] > 0
    assert report["counts"]["quarantined"] == len(quarantine.splitlines())


def test_cli_rejects_missing_input(tmp_path: Path) -> None:
    assert (
        main(
            [
                "--input",
                str(tmp_path / "nope.txt"),
                "--personas",
                str(REPO_ROOT / "configs" / "personas.yaml"),
                "--character",
                "character_a",
                "--source-id",
                "x",
                "--series",
                "x",
                "--episode",
                "1",
                "--out",
                str(tmp_path),
            ]
        )
        == 2
    )
