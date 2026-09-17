"""Deterministic JSONL / report writers.

Byte-for-byte reproducibility matters: Loop 2's gate is that re-running the
same input yields the same output hash.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..canonical.models import ParseResult


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(_dump(row) + "\n")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def write_parse_result(out_dir: Path, result: ParseResult, source_id: str) -> dict[str, Path]:
    """Write utterances, quarantine and report. Returns the paths written."""
    paths = {
        "utterances": out_dir / "parsed" / f"{source_id}.jsonl",
        "quarantine": out_dir / "quarantine" / f"{source_id}.jsonl",
        "report": out_dir / "reports" / f"{source_id}.json",
    }
    write_jsonl(paths["utterances"], [u.to_dict() for u in result.utterances])
    write_jsonl(paths["quarantine"], [q.to_dict() for q in result.quarantine])
    if result.report is not None:
        write_json(paths["report"], result.report.to_dict())
    return paths


def write_run_report(out_dir: Path, report: Any) -> Path:
    """Write the run-level audit record.

    Separate from the per-source parse reports: this one answers
    "what dataset did this run produce, from which sources".
    """
    path = out_dir / "run_report.json"
    write_json(path, report.to_dict())
    return path


def write_parquet(path: Path, rows: list[dict[str, Any]]) -> Path:
    """Optional columnar output.

    JSONL is the working format — the corpus is small enough that Parquet buys
    nothing today. This exists for when it is not, and sits behind an optional
    dependency so the default install stays light.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise RuntimeError(
            "Parquet output requires the 'parquet' extra: uv sync --extra parquet"
        ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = [{**row, "source_locator": _dump(row["source_locator"])} for row in rows]
    pq.write_table(pa.Table.from_pylist(normalized), path)
    return path
