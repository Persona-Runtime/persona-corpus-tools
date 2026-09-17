from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..reporting.markdown_export import render_wiki_markdown
from .extractor import ExtractionError, extract_acquisition, load_extraction


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="persona-wiki-extract", description="Extract acquired wiki HTML for review."
    )
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--format",
        choices=("jsonl", "paste"),
        default="jsonl",
        help=(
            "jsonl: private structured extraction for review (default). "
            "paste: single markdown file to paste into the web app; --out is "
            "the file path, not a directory."
        ),
    )
    args = parser.parse_args(argv)
    try:
        if args.format == "paste":
            documents, paragraphs, _report = load_extraction(args.input_root)
            text = render_wiki_markdown(documents, paragraphs)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote paste markdown -> {args.out}")
            return 0
        report = extract_acquisition(args.input_root, args.out)
    except (ExtractionError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"extracted {report['totals']['paragraphs']} paragraph(s) -> {args.out}")
    return 0
