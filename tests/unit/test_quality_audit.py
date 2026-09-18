from __future__ import annotations

import json
from pathlib import Path

from persona_corpus_tools.canonical.versioning import compute_corpus_version
from persona_corpus_tools.quality.audit import build_audit
from persona_corpus_tools.quality.cli import main


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )


def _utterance(source_id: str, utterance_id: str, text: str) -> dict[str, object]:
    return {
        "dataset_version": f"ds1-{source_id}",
        "source_id": source_id,
        "source_locator": {"line": 1, "timestamp": "00:01", "timestamp_seconds": 1},
        "text": text,
        "utterance_id": utterance_id,
    }


def _quarantine(source_id: str, reason: str = "text_before_first_header") -> dict[str, object]:
    return {
        "dataset_version": f"ds1-{source_id}",
        "line_no": 3,
        "raw": "PRIVATE RAW QUARANTINE TEXT",
        "reason": reason,
        "source_id": source_id,
    }


def _write_corpus(root: Path) -> None:
    parsed = {
        "source-a": [_utterance("source-a", "source-a-t00001-0001", "same")],
        "source-b": [
            _utterance("source-b", "source-b-t00001-0001", "same"),
            _utterance(
                "source-b",
                "source-b-t00002-0002",
                "This utterance is deliberately longer than twenty characters.",
            ),
        ],
    }
    quarantine = {"source-a": [_quarantine("source-a")], "source-b": []}
    sources = []
    for source_id in sorted(parsed):
        _write_jsonl(root / "parsed" / f"{source_id}.jsonl", parsed[source_id])
        _write_jsonl(root / "quarantine" / f"{source_id}.jsonl", quarantine[source_id])
        sources.append(
            {
                "dataset_version": f"ds1-{source_id}",
                "quarantined": len(quarantine[source_id]),
                "source_id": source_id,
                "target": len(parsed[source_id]),
            }
        )
    _write_json(
        root / "run_report.json",
        {
            "dataset_version": compute_corpus_version(
                {str(source["source_id"]): str(source["dataset_version"]) for source in sources}
            ),
            "sources": sources,
        },
    )


def test_report_only_audit_groups_duplicates_without_copying_text(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)

    report, candidates = build_audit(corpus, short_text_max=20)

    assert report["totals"] == {
        "exact_duplicate_groups": 1,
        "exact_duplicate_utterances": 2,
        "quarantine": 1,
        "review_candidates": 3,
        "short_utterances": 2,
        "sources": 2,
        "utterances": 3,
    }
    assert report["quarantine_reasons"] == {"text_before_first_header": 1}
    assert report["text_length_buckets"] == {
        "0-20": 2,
        "21-50": 0,
        "51-100": 1,
        "101-200": 0,
        "201+": 0,
    }
    serialized = json.dumps({"report": report, "candidates": candidates})
    assert "PRIVATE RAW QUARANTINE TEXT" not in serialized
    assert '"text"' not in serialized
    duplicate_rows = [row for row in candidates if row["candidate_type"] == "utterance"]
    assert all(row["reasons"] == ["short_text", "exact_duplicate"] for row in duplicate_rows)
    assert all(row["duplicate_count"] == 2 for row in duplicate_rows)


def test_audit_output_is_byte_identical(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    first = tmp_path / "first"
    second = tmp_path / "second"

    assert main(["--corpus", str(corpus), "--out", str(first)]) == 0
    assert main(["--corpus", str(corpus), "--out", str(second)]) == 0

    for name in ("quality_report.json", "review_candidates.jsonl"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_mismatched_provenance_fails_without_output(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    bad = corpus / "parsed" / "source-a.jsonl"
    row = json.loads(bad.read_text(encoding="utf-8"))
    row["dataset_version"] = "wrong-version"
    _write_jsonl(bad, [row])

    out = tmp_path / "audit"
    assert main(["--corpus", str(corpus), "--out", str(out)]) == 2
    assert not out.exists()


def test_mismatched_corpus_version_or_source_set_fails_without_output(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    report_path = corpus / "run_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    report["dataset_version"] = "ds1corpus-wrong"
    _write_json(report_path, report)
    assert main(["--corpus", str(corpus), "--out", str(tmp_path / "wrong-version")]) == 2

    _write_corpus(corpus)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["sources"] = report["sources"][:1]
    _write_json(report_path, report)
    assert main(["--corpus", str(corpus), "--out", str(tmp_path / "missing-source")]) == 2


def test_locator_allowlist_does_not_copy_unknown_content(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    path = corpus / "parsed" / "source-a.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["source_locator"]["secret"] = "PRIVATE LOCATOR TEXT"
    _write_jsonl(path, [row])

    report, candidates = build_audit(corpus, short_text_max=20)

    serialized = json.dumps({"report": report, "candidates": candidates})
    assert "PRIVATE LOCATOR TEXT" not in serialized
    utterance = next(row for row in candidates if row["candidate_type"] == "utterance")
    assert utterance["source_locator"] == {"line": 1, "timestamp": "00:01", "timestamp_seconds": 1}


def test_refuses_overlapping_output_without_changing_corpus(tmp_path: Path) -> None:
    root = tmp_path / "audit-root"
    corpus = root / "corpus"
    _write_corpus(corpus)
    before = (corpus / "run_report.json").read_bytes()

    for out in (corpus, root, corpus / "nested-audit"):
        assert main(["--corpus", str(corpus), "--out", str(out)]) == 2
        assert (corpus / "run_report.json").read_bytes() == before
        assert (corpus / "parsed" / "source-a.jsonl").is_file()


def test_refuses_extra_files_but_replaces_complete_prior_audit(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    out = tmp_path / "audit"
    out.mkdir()
    (out / "quality_report.json").write_text("{}\n", encoding="utf-8")
    (out / "review_candidates.jsonl").write_text("\n", encoding="utf-8")
    extra = out / "keep.txt"
    extra.write_text("keep", encoding="utf-8")

    assert main(["--corpus", str(corpus), "--out", str(out)]) == 2
    assert extra.read_text(encoding="utf-8") == "keep"

    extra.unlink()
    assert main(["--corpus", str(corpus), "--out", str(out)]) == 0
    assert "audit_version" in (out / "quality_report.json").read_text(encoding="utf-8")
