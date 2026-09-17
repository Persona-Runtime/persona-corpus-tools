"""Deterministic, read-only quality audit for a completed ingestion corpus."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..canonical.versioning import compute_corpus_version
from ..reporting.writer import write_json, write_jsonl

AUDIT_SCHEMA_VERSION = "1"
AUDIT_VERSION = "quality-audit-v1"
_TRANSCRIPT_LOCATOR_FIELDS = ("timestamp", "timestamp_seconds", "line")
_PDF_LOCATOR_FIELDS = ("page", "page_line", "cue")
_SMI_LOCATOR_FIELDS = ("subtitle_id", "start_ms", "end_ms", "source_line", "start_char", "end_char")


class AuditError(ValueError):
    """The corpus is not a complete canonical ingestion output."""


def build_audit(corpus: Path, short_text_max: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate a corpus and return its report plus text-free review references."""
    if short_text_max < 0:
        raise AuditError("--short-text-max must be zero or greater")

    run_report = _load_json(corpus / "run_report.json")
    corpus_version = _required_string(run_report, "dataset_version", "run_report.json")
    sources_raw = run_report.get("sources")
    if not isinstance(sources_raw, list) or not sources_raw:
        raise AuditError("run_report.json: 'sources' must be a non-empty list")
    if not all(isinstance(source, dict) for source in sources_raw):
        raise AuditError("run_report.json: each source must be an object")

    sources = sorted(
        sources_raw, key=lambda source: _required_string(source, "source_id", "source")
    )
    source_ids: set[str] = set()
    summaries: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    utterances: list[dict[str, Any]] = []
    quarantine_reasons: Counter[str] = Counter()
    source_versions: dict[str, str] = {}

    for source in sources:
        source_id = _required_string(source, "source_id", "run_report source")
        if source_id in source_ids:
            raise AuditError(f"run_report.json: duplicate source_id {source_id!r}")
        source_ids.add(source_id)
        dataset_version = _required_string(source, "dataset_version", source_id)
        source_versions[source_id] = dataset_version

        parsed = _load_jsonl(corpus / "parsed" / f"{source_id}.jsonl")
        quarantined = _load_jsonl(corpus / "quarantine" / f"{source_id}.jsonl")
        _validate_source_rows(parsed, source_id, dataset_version, "parsed")
        _validate_source_rows(quarantined, source_id, dataset_version, "quarantine")
        _validate_report_count(source, "target", len(parsed), source_id)
        _validate_report_count(source, "quarantined", len(quarantined), source_id)

        source_reasons: Counter[str] = Counter()
        for row in quarantined:
            reason = _required_string(row, "reason", f"quarantine {source_id}")
            line_no = row.get("line_no")
            if not isinstance(line_no, int):
                raise AuditError(f"quarantine {source_id}: 'line_no' must be an integer")
            source_reasons[reason] += 1
            quarantine_reasons[reason] += 1
            candidates.append(
                {
                    "candidate_type": "quarantine",
                    "dataset_version": dataset_version,
                    "line_no": line_no,
                    "reason": reason,
                    "source_id": source_id,
                }
            )

        for row in parsed:
            utterance_id = _required_string(row, "utterance_id", f"parsed {source_id}")
            text = row.get("text")
            if not isinstance(text, str):
                raise AuditError(f"parsed {source_id}: 'text' must be a string")
            utterances.append(
                {
                    "char_count": len(text),
                    "dataset_version": dataset_version,
                    "source_id": source_id,
                    "source_locator": _sanitize_locator(row.get("source_locator"), source_id),
                    "text": text,
                    "utterance_id": utterance_id,
                }
            )

        summaries.append(
            {
                "dataset_version": dataset_version,
                "quarantine": len(quarantined),
                "quarantine_reasons": dict(sorted(source_reasons.items())),
                "source_id": source_id,
                "utterances": len(parsed),
            }
        )

    expected_corpus_version = compute_corpus_version(source_versions)
    if corpus_version != expected_corpus_version:
        raise AuditError(
            "run_report.json: corpus dataset_version does not match the declared source versions"
        )

    duplicate_sizes = Counter(_text_digest(row["text"]) for row in utterances)
    duplicate_groups = sum(size > 1 for size in duplicate_sizes.values())
    duplicate_utterances = sum(size for size in duplicate_sizes.values() if size > 1)
    short_utterances = 0
    per_source_quality: dict[str, Counter[str]] = defaultdict(Counter)

    for row in utterances:
        digest = _text_digest(row["text"])
        duplicate_count = duplicate_sizes[digest]
        reasons: list[str] = []
        if row["char_count"] <= short_text_max:
            short_utterances += 1
            per_source_quality[row["source_id"]]["short_utterances"] += 1
            reasons.append("short_text")
        if duplicate_count > 1:
            per_source_quality[row["source_id"]]["duplicate_utterances"] += 1
            reasons.append("exact_duplicate")
        if reasons:
            candidates.append(
                {
                    "candidate_type": "utterance",
                    "char_count": row["char_count"],
                    "dataset_version": row["dataset_version"],
                    "duplicate_count": duplicate_count,
                    "reasons": reasons,
                    "source_id": row["source_id"],
                    "source_locator": row["source_locator"],
                    "text_sha256": digest,
                    "utterance_id": row["utterance_id"],
                }
            )

    for summary in summaries:
        quality = per_source_quality[summary["source_id"]]
        summary["duplicate_utterances"] = quality["duplicate_utterances"]
        summary["short_utterances"] = quality["short_utterances"]

    report = {
        "audit_version": AUDIT_VERSION,
        "corpus_dataset_version": corpus_version,
        "schema_version": AUDIT_SCHEMA_VERSION,
        "short_text_max": short_text_max,
        "sources": summaries,
        "text_length_buckets": _length_buckets(row["char_count"] for row in utterances),
        "totals": {
            "exact_duplicate_groups": duplicate_groups,
            "exact_duplicate_utterances": duplicate_utterances,
            "quarantine": sum(summary["quarantine"] for summary in summaries),
            "review_candidates": len(candidates),
            "short_utterances": short_utterances,
            "sources": len(summaries),
            "utterances": len(utterances),
        },
        "quarantine_reasons": dict(sorted(quarantine_reasons.items())),
    }
    return report, sorted(candidates, key=_candidate_sort_key)


def write_audit(corpus: Path, out: Path, short_text_max: int) -> dict[str, Any]:
    """Build all artifacts before atomically replacing an existing audit directory."""
    corpus = corpus.resolve()
    out = out.resolve()
    _assert_disjoint(corpus, out)
    _assert_replaceable(out)
    report, candidates = build_audit(corpus, short_text_max)
    staging = out.parent / f".{out.name}.tmp-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        write_json(staging / "quality_report.json", report)
        write_jsonl(staging / "review_candidates.jsonl", candidates)
        _swap_into_place(staging, out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return report


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AuditError(f"{path}: expected an object")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AuditError(f"cannot read {path}: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        try:
            row: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AuditError(f"{path}:{number}: invalid JSON") from exc
        if not isinstance(row, dict):
            raise AuditError(f"{path}:{number}: expected an object")
        rows.append(row)
    return rows


def _required_string(record: dict[str, Any], field: str, context: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise AuditError(f"{context}: '{field}' must be a non-empty string")
    return value


def _validate_source_rows(
    rows: list[dict[str, Any]], source_id: str, dataset_version: str, kind: str
) -> None:
    for row in rows:
        if row.get("source_id") != source_id:
            raise AuditError(f"{kind} {source_id}: record has a mismatched source_id")
        if row.get("dataset_version") != dataset_version:
            raise AuditError(f"{kind} {source_id}: record has a mismatched dataset_version")


def _validate_report_count(source: dict[str, Any], field: str, actual: int, source_id: str) -> None:
    expected = source.get(field)
    if not isinstance(expected, int):
        raise AuditError(f"run_report {source_id}: '{field}' must be an integer")
    if expected != actual:
        raise AuditError(
            f"run_report {source_id}: '{field}' is {expected}, but corpus contains {actual}"
        )


def _sanitize_locator(value: Any, source_id: str) -> dict[str, Any]:
    """Copy only the locator shapes produced by the supported adapters."""
    if not isinstance(value, dict):
        raise AuditError(f"parsed {source_id}: 'source_locator' must be an object")
    if _has_fields(value, _TRANSCRIPT_LOCATOR_FIELDS, (str, int, int)):
        return {field: value[field] for field in _TRANSCRIPT_LOCATOR_FIELDS}
    if _has_fields(value, _PDF_LOCATOR_FIELDS, (int, int, str)):
        return {field: value[field] for field in _PDF_LOCATOR_FIELDS}
    if _has_fields(value, _SMI_LOCATOR_FIELDS, (str, int, int, int, int, int)):
        return {field: value[field] for field in _SMI_LOCATOR_FIELDS}
    raise AuditError(f"parsed {source_id}: unsupported source_locator shape")


def _has_fields(
    value: dict[str, Any], fields: tuple[str, ...], types: tuple[type[object], ...]
) -> bool:
    return all(
        type(value.get(field)) is expected_type
        for field, expected_type in zip(fields, types, strict=True)
    )


def _text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _length_buckets(lengths: Any) -> dict[str, int]:
    buckets = Counter[str]()
    for length in lengths:
        if length <= 20:
            buckets["0-20"] += 1
        elif length <= 50:
            buckets["21-50"] += 1
        elif length <= 100:
            buckets["51-100"] += 1
        elif length <= 200:
            buckets["101-200"] += 1
        else:
            buckets["201+"] += 1
    return {bucket: buckets[bucket] for bucket in ("0-20", "21-50", "51-100", "101-200", "201+")}


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[str, str, int, str]:
    return (
        candidate["source_id"],
        candidate["candidate_type"],
        candidate.get("line_no", 0),
        candidate.get("utterance_id", ""),
    )


def _assert_replaceable(out: Path) -> None:
    if not out.exists():
        return
    if not out.is_dir():
        raise AuditError(f"--out exists and is not a directory: {out}")
    entries = {entry.name: entry for entry in out.iterdir()}
    expected = {"quality_report.json", "review_candidates.jsonl"}
    if entries and (
        set(entries) != expected or not all(entry.is_file() for entry in entries.values())
    ):
        raise AuditError(f"--out is not a complete audit output directory: {out}")


def _assert_disjoint(corpus: Path, out: Path) -> None:
    if corpus == out or corpus.is_relative_to(out) or out.is_relative_to(corpus):
        raise AuditError("--corpus and --out must be disjoint paths")


def _swap_into_place(staging: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    previous = out.parent / f".{out.name}.old-{os.getpid()}"
    replaced = False
    if out.exists():
        out.rename(previous)
        replaced = True
    try:
        staging.rename(out)
    except BaseException:
        if replaced:
            previous.rename(out)
        raise
    if replaced:
        shutil.rmtree(previous, ignore_errors=True)
