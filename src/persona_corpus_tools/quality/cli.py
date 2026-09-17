"""Command-line entry point for private corpus quality audits."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .audit import AuditError, write_audit

EXIT_OK = 0
EXIT_INPUT = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="persona-audit",
        description="Audit a canonical ingestion corpus without changing its records.",
    )
    parser.add_argument("--corpus", type=Path, required=True, help="ingestion output directory")
    parser.add_argument("--out", type=Path, required=True, help="private audit output directory")
    parser.add_argument(
        "--short-text-max",
        type=int,
        default=20,
        help="flag utterances with this many characters or fewer (default: 20)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = write_audit(args.corpus, args.out, args.short_text_max)
    except AuditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    print(
        f"audit {report['corpus_dataset_version']} "
        f"({report['totals']['utterances']} utterance(s)) -> {args.out / 'quality_report.json'}"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
