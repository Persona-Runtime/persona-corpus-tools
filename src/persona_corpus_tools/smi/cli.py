"""Entry points for the intentionally local SMI speaker-review workflow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..reporting.markdown_export import render_speech_lines
from .export import export, load_export
from .prepare import prepare
from .reviewer import create_app
from .storage import ReviewError


def prepare_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="persona-smi-prepare")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        prepare(args.manifest, args.private_root, args.out)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"prepared subtitles -> {args.out}")
    return 0


def review_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="persona-smi-review")
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--media-root", type=Path)
    parser.add_argument("--media-file")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.media_file and not args.media_root:
        parser.error("--media-file requires --media-root")
    import uvicorn

    try:
        app = create_app(args.prepared, args.annotations, args.media_root, args.media_file)
    except (ReviewError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def export_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="persona-smi-export")
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--personas", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--format",
        choices=("jsonl", "paste"),
        default="jsonl",
        help=(
            "jsonl: private structured export for review (default). "
            "paste: single '화자: 대사' text file to paste into the web app; "
            "--out is the file path, not a directory."
        ),
    )
    args = parser.parse_args(argv)
    try:
        if args.format == "paste":
            version, utterances = load_export(args.prepared, args.annotations, args.personas)
            text = render_speech_lines(
                [(u.speaker_normalized, u.text) for u in utterances]
            )
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {len(utterances)} speech line(s) ({version}) -> {args.out}")
            return 0
        version = export(args.prepared, args.annotations, args.personas, args.out)
    except (ReviewError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"exported {version} -> {args.out}")
    return 0
