from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .extractor import ExtractionError, extract_acquisition


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="persona-wiki-extract", description="Extract acquired wiki HTML for review."
    )
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = extract_acquisition(args.input_root, args.out)
    except (ExtractionError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"extracted {report['totals']['paragraphs']} paragraph(s) -> {args.out}")
    return 0
