"""Prepared-subtitle and annotation files.  They live outside the corpus."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ..reporting.writer import write_json, write_jsonl

LABELS = frozenset({"gintoki", "other", "non_dialogue", "undetermined"})


class ReviewError(ValueError):
    pass


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_prepared(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        metadata = json.loads((directory / "source.json").read_text(encoding="utf-8"))
        rows = [
            json.loads(line)
            for line in (directory / "subtitles.jsonl").read_text(encoding="utf-8").splitlines()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"cannot read prepared subtitles: {exc}") from exc
    if not isinstance(metadata, dict) or not isinstance(rows, list):
        raise ReviewError("prepared subtitles have invalid shape")
    return metadata, rows


def empty_annotations(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "source_id": metadata["source_id"],
        "source_sha256": metadata["source_sha256"],
        "parser_version": metadata["parser_version"],
        "subtitles": {},
    }


def load_annotations(path: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return empty_annotations(metadata)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"cannot read annotations: {exc}") from exc
    if not isinstance(value, dict) or value.get("source_sha256") != metadata["source_sha256"]:
        raise ReviewError("annotations are stale or do not match this prepared source")
    return value


def validate_segments(text: str, segments: object) -> list[dict[str, object]]:
    if not isinstance(segments, list) or not segments:
        raise ReviewError("segments must be a non-empty list")
    result: list[dict[str, object]] = []
    cursor = 0
    for segment in segments:
        if not isinstance(segment, dict):
            raise ReviewError("each segment must be an object")
        start, end, label = segment.get("start_char"), segment.get("end_char"), segment.get("label")
        if not isinstance(start, int) or not isinstance(end, int) or label not in LABELS:
            raise ReviewError("segment has invalid range or label")
        if start != cursor or end <= start or end > len(text):
            raise ReviewError("segments must be contiguous, non-empty, and cover the cue exactly")
        cursor = end
        result.append(
            {
                "start_char": start,
                "end_char": end,
                "label": label,
                "review_state": "needs_video" if label == "undetermined" else "confirmed",
            }
        )
    if cursor != len(text):
        raise ReviewError("segments must cover the cue exactly")
    return result


def save_annotation(
    path: Path, metadata: dict[str, Any], row: dict[str, Any], segments: object
) -> dict[str, Any]:
    if row["is_clear"]:
        raise ReviewError("blank clear cues cannot be labelled")
    doc = load_annotations(path, metadata)
    validated = validate_segments(str(row["visible_text"]), segments)
    subtitles = doc.setdefault("subtitles", {})
    subtitles[row["subtitle_id"]] = {
        "text_sha256": digest_text(str(row["visible_text"])),
        "segments": validated,
    }
    _atomic_json(path, doc)
    return doc


def validate_complete(
    metadata: dict[str, Any], rows: list[dict[str, Any]], annotations: dict[str, Any]
) -> None:
    if annotations.get("source_sha256") != metadata.get("source_sha256"):
        raise ReviewError("annotations are stale")
    entries = annotations.get("subtitles")
    if not isinstance(entries, dict):
        raise ReviewError("annotations have invalid subtitles")
    for row in rows:
        if row["is_clear"]:
            continue
        entry = entries.get(row["subtitle_id"])
        if not isinstance(entry, dict) or entry.get("text_sha256") != digest_text(
            row["visible_text"]
        ):
            raise ReviewError(f"{row['subtitle_id']}: missing or stale annotation")
        validate_segments(row["visible_text"], entry.get("segments"))


def write_prepared(directory: Path, metadata: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "source.json", metadata)
    write_jsonl(directory / "subtitles.jsonl", rows)


def _atomic_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.tmp-{os.getpid()}"
    write_json(temp, doc)
    temp.replace(path)
