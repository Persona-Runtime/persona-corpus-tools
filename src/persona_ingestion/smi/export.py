from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..canonical.models import ParseReport, ParseResult, QuarantineRecord, RunReport, Utterance
from ..canonical.normalize import normalize_text
from ..canonical.personas import load_persona_config
from ..canonical.versioning import (
    compute_corpus_version,
    compute_reviewed_smi_dataset_version,
    file_digest,
)
from ..reporting.writer import write_parse_result, write_run_report
from .storage import ReviewError, load_annotations, load_prepared, validate_complete


def export(prepared: Path, annotations_path: Path, personas: Path, out: Path) -> str:
    metadata, rows = load_prepared(prepared)
    annotations = load_annotations(annotations_path, metadata)
    validate_complete(metadata, rows, annotations)
    config = load_persona_config(personas)
    if metadata["character_id"] not in config.persona_ids:
        raise ReviewError("prepared character_id is absent from persona config")
    version = compute_reviewed_smi_dataset_version(
        parser_version=metadata["parser_version"],
        persona_config_digest=file_digest(personas),
        manifest_digest=metadata["manifest_digest"],
        annotation_digest=file_digest(annotations_path),
    )
    report = ParseReport(
        metadata["source_id"], metadata["character_id"], metadata["parser_version"]
    )
    utterances: list[Utterance] = []
    quarantine: list[QuarantineRecord] = []
    entry_map = annotations["subtitles"]
    for row in rows:
        if row["is_clear"]:
            continue
        for n, segment in enumerate(entry_map[row["subtitle_id"]]["segments"], 1):
            text = normalize_text(row["visible_text"][segment["start_char"] : segment["end_char"]])
            label = segment["label"]
            if label != "gintoki":
                report.bump(label)
                continue
            if row["timing_issue"] or not text:
                quarantine.append(
                    QuarantineRecord(
                        metadata["source_id"],
                        row["source_line"],
                        "invalid_timing" if row["timing_issue"] else "empty_segment",
                        text,
                        metadata["parser_version"],
                        metadata["source_sha256"],
                        version,
                    )
                )
                report.bump("quarantined")
                continue
            locator = {
                key: row[key] for key in ("subtitle_id", "start_ms", "end_ms", "source_line")
            }
            locator.update({"start_char": segment["start_char"], "end_char": segment["end_char"]})
            utterances.append(
                Utterance(
                    f"{metadata['source_id']}-{row['subtitle_id']}-{n:02d}",
                    metadata["character_id"],
                    metadata["series"],
                    metadata["episode"],
                    metadata["source_id"],
                    locator,
                    "reviewed:gintoki",
                    "gintoki",
                    metadata["language"],
                    text,
                    metadata["parser_version"],
                    metadata["source_sha256"],
                    version,
                )
            )
            report.bump("target")
    result = ParseResult(utterances, quarantine, report)
    write_parse_result(out, result, metadata["source_id"])
    corpus_version = compute_corpus_version({metadata["source_id"]: version})
    write_run_report(
        out,
        RunReport(
            corpus_version,
            [
                {
                    "source_id": metadata["source_id"],
                    "dataset_version": version,
                    "target": len(utterances),
                    "quarantined": len(quarantine),
                }
            ],
            dict(Counter(target=len(utterances), quarantined=len(quarantine))),
        ),
    )
    return corpus_version
