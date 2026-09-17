from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from persona_ingestion.adapters.errors import AdapterParseError
from persona_ingestion.adapters.registry import ADAPTERS
from persona_ingestion.canonical.models import ParseResult, SourceMeta
from persona_ingestion.canonical.personas import PersonaConfig
from persona_ingestion.cli.main import main
from persona_ingestion.intake.hashing import sha256_file

from ..conftest import EXPECTED_DIR, MANIFEST_FIXTURE, PERSONAS, PRIVATE_ROOT

SOURCE_ID = "sample-series-ep001"


def run(out: Path, manifest: Path = MANIFEST_FIXTURE, *, manifest_dir: Path | None = None) -> int:
    where = ["--manifest-dir", str(manifest_dir)] if manifest_dir else ["--manifest", str(manifest)]
    return main(
        [
            *where,
            "--private-root",
            str(PRIVATE_ROOT),
            "--personas",
            str(PERSONAS),
            "--out",
            str(out),
        ]
    )


def artifacts(out: Path, source_id: str = SOURCE_ID) -> dict[str, Path]:
    return {
        "utterances": out / "parsed" / f"{source_id}.jsonl",
        "quarantine": out / "quarantine" / f"{source_id}.jsonl",
        "report": out / "reports" / f"{source_id}.json",
        "run_report": out / "run_report.json",
    }


def variant(tmp_path: Path, name: str, *replacements: tuple[str, str]) -> Path:
    """A copy of the fixture manifest with substitutions applied."""
    body = MANIFEST_FIXTURE.read_text(encoding="utf-8")
    for old, new in replacements:
        assert old in body, f"substitution target not found: {old!r}"
        body = body.replace(old, new)
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


EXPIRED = ("delete_at: 2099-12-31", "delete_at: 2020-01-01")
WRONG_HASH = (
    'sha256: "fb417dfcc806d7cc14ad04481ebcfa143090f9eb0be544ded14b157c4b9b42e1"',
    'sha256: "' + "0" * 64 + '"',
)


def test_produces_every_expected_artifact(tmp_path: Path) -> None:
    assert run(tmp_path / "out") == 0
    for name, path in artifacts(tmp_path / "out").items():
        assert path.is_file(), name


GOLDEN_UTTERANCES = EXPECTED_DIR / "sample_series_ep001.utterances.jsonl"
GOLDEN_QUARANTINE = EXPECTED_DIR / "sample_series_ep001.quarantine.jsonl"


def test_matches_golden_output(tmp_path: Path) -> None:
    """Catches unintended output changes, including to quarantine — which
    holds source text and so is as much a deliverable as the utterances."""
    out = tmp_path / "out"
    run(out)
    assert artifacts(out)["utterances"].read_text("utf-8") == GOLDEN_UTTERANCES.read_text("utf-8")
    assert artifacts(out)["quarantine"].read_text("utf-8") == GOLDEN_QUARANTINE.read_text("utf-8")


# --- Loop 2 완료 조건: 재현성 ------------------------------------------------


def test_two_runs_are_byte_identical(tmp_path: Path) -> None:
    """Same input, same bytes — across every artifact, not just utterances."""
    hashes = []
    for name in ("a", "b"):
        out = tmp_path / name
        assert run(out) == 0
        hashes.append({k: sha256_file(p) for k, p in artifacts(out).items()})
    assert hashes[0] == hashes[1]


def test_run_report_has_no_wall_clock_fields(tmp_path: Path) -> None:
    run(tmp_path / "out")
    report = json.loads(artifacts(tmp_path / "out")["run_report"].read_text("utf-8"))
    assert not {"created_at", "generated_at", "timestamp", "started_at"} & set(report)


# --- provenance travels with the data ---------------------------------------


def test_utterances_and_quarantine_share_the_same_provenance(tmp_path: Path) -> None:
    """Quarantine holds source text too, so it carries the same fields."""
    out = tmp_path / "out"
    run(out)
    rows = [json.loads(x) for x in artifacts(out)["utterances"].read_text("utf-8").splitlines()]
    quar = [json.loads(x) for x in artifacts(out)["quarantine"].read_text("utf-8").splitlines()]
    assert rows and quar

    expected = json.loads(artifacts(out)["run_report"].read_text("utf-8"))["sources"][0]
    for record in rows + quar:
        assert record["dataset_version"] == expected["dataset_version"]
        assert record["source_sha256"] == expected["source_sha256"]


def test_run_report_is_auditable(tmp_path: Path) -> None:
    out = tmp_path / "out"
    run(out)
    report = json.loads(artifacts(out)["run_report"].read_text("utf-8"))
    assert report["dataset_version"].startswith("ds1corpus-")
    source = report["sources"][0]
    assert source["rights_status"] == "synthetic"
    assert source["parser_profile"] == "transcript-txt"
    assert source["parser_version"] == "transcript-txt-v2"
    # dataset_version을 재계산해 검증할 수 있도록 재료가 남아 있어야 한다
    assert source["manifest_digest"] and source["persona_config_digest"]


# --- 게이트: 하나라도 실패하면 아무것도 쓰지 않는다 ---------------------------


def test_hash_mismatch_stops_the_run(tmp_path: Path) -> None:
    manifest = variant(tmp_path, "bad.yaml", WRONG_HASH)
    assert run(tmp_path / "out", manifest) == 3
    assert not (tmp_path / "out").exists()


def test_expired_retention_stops_the_run(tmp_path: Path) -> None:
    manifest = variant(tmp_path, "old.yaml", EXPIRED)
    assert run(tmp_path / "out", manifest) == 3
    assert not (tmp_path / "out").exists()


def test_unimplemented_adapter_is_reported_separately(tmp_path: Path) -> None:
    manifest = variant(
        tmp_path,
        "pdf.yaml",
        ("input_type: transcript_txt", "input_type: script_pdf"),
        ("parser_profile: transcript-txt", "parser_profile: script-pdf"),
    )
    assert run(tmp_path / "out", manifest) == 4
    assert not (tmp_path / "out").exists()


def test_parse_failure_leaves_no_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verified source can still be structurally unusable (for example a scan)."""

    def broken_parser(
        data: bytes,
        meta: SourceMeta,
        personas: PersonaConfig,
        target_character_id: str,
    ) -> ParseResult:
        raise AdapterParseError("sample-series-ep001: no extractable screenplay text")

    original = ADAPTERS["transcript-txt"]
    monkeypatch.setitem(
        ADAPTERS, "transcript-txt", dataclasses.replace(original, parse=broken_parser)
    )

    out = tmp_path / "out"
    assert run(out) == 5
    assert not out.exists()


def test_bad_manifest_exits_before_touching_data(tmp_path: Path) -> None:
    manifest = tmp_path / "broken.yaml"
    manifest.write_text("source_id: x\n", encoding="utf-8")
    assert run(tmp_path / "out", manifest) == 2
    assert not (tmp_path / "out").exists()


# --- 원자성: 부분 산출물이 남지 않는다 ----------------------------------------


def test_a_failing_second_source_leaves_nothing_behind(tmp_path: Path) -> None:
    """The review's finding: source 1 used to be written to disk before
    source 2's gate failed, leaving a partial corpus."""
    mdir = tmp_path / "manifests"
    mdir.mkdir()
    (mdir / "a.yaml").write_text(MANIFEST_FIXTURE.read_text("utf-8"), encoding="utf-8")
    bad = MANIFEST_FIXTURE.read_text("utf-8").replace(
        "source_id: sample-series-ep001", "source_id: zz-broken"
    )
    bad = bad.replace("delete_at: 2099-12-31", "delete_at: 2020-01-01")
    (mdir / "z.yaml").write_text(bad, encoding="utf-8")

    out = tmp_path / "out"
    assert run(out, manifest_dir=mdir) == 3
    assert not out.exists(), "partial corpus was left behind"


def test_a_failed_rerun_does_not_damage_the_previous_result(tmp_path: Path) -> None:
    """A good run, then a failing one. The old corpus must survive intact."""
    out = tmp_path / "out"
    assert run(out) == 0
    before = {k: sha256_file(p) for k, p in artifacts(out).items()}

    manifest = variant(tmp_path, "old.yaml", EXPIRED)
    assert run(out, manifest) == 3

    after = {k: sha256_file(p) for k, p in artifacts(out).items()}
    assert before == after


def test_a_successful_rerun_replaces_stale_artifacts(tmp_path: Path) -> None:
    """The output directory is the complete result of one run, never a mix."""
    out = tmp_path / "out"
    assert run(out) == 0
    stale = out / "parsed" / "left-over-from-an-older-run.jsonl"
    stale.write_text("{}\n", encoding="utf-8")

    assert run(out) == 0
    assert not stale.exists()


def test_refuses_to_replace_a_directory_that_is_not_ours(tmp_path: Path) -> None:
    """A typo in --out must not be able to destroy an unrelated folder."""
    out = tmp_path / "documents"
    out.mkdir()
    (out / "important.txt").write_text("do not delete", encoding="utf-8")

    assert run(out) == 2
    assert (out / "important.txt").read_text(encoding="utf-8") == "do not delete"


@pytest.mark.parametrize("leftover", [".out.tmp-1", ".out.old-1"])
def test_no_staging_directories_survive_a_successful_run(tmp_path: Path, leftover: str) -> None:
    out = tmp_path / "out"
    assert run(out) == 0
    assert not (tmp_path / leftover).exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".out")] == []
