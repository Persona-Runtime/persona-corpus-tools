"""Loop 1 CLI: parse one local transcript file into canonical records.

Source identity is supplied on the command line here. Loop 2 replaces these
flags with ``source_manifest.yaml``; the adapter contract does not change.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..adapters.transcript_txt import PARSER_VERSION, parse_transcript_file
from ..canonical.models import SourceMeta
from ..canonical.personas import ConfigError, load_persona_config
from ..intake.hashing import sha256_file
from ..reporting.writer import write_parse_result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="persona-ingest",
        description="Parse a local transcript file into canonical persona utterances.",
    )
    parser.add_argument("--input", type=Path, required=True, help="local transcript .txt file")
    parser.add_argument("--personas", type=Path, required=True, help="persona alias config YAML")
    parser.add_argument("--character", required=True, help="target persona id")
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--series", required=True)
    parser.add_argument("--episode", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--out", type=Path, required=True, help="output root directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.input.is_file():
        print(f"error: input not found: {args.input}", file=sys.stderr)
        return 2

    try:
        personas = load_persona_config(args.personas)
    except (OSError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    meta = SourceMeta(
        source_id=args.source_id,
        series=args.series,
        episode=args.episode,
        language=args.language,
        sha256=sha256_file(args.input),
        input_type="transcript_txt",
    )

    result = parse_transcript_file(args.input, meta, personas, args.character)
    paths = write_parse_result(args.out, result, meta.source_id)

    counts = result.report.counts if result.report else {}
    print(
        f"{meta.source_id}: parser={PARSER_VERSION} "
        f"target={counts.get('target', 0)} "
        f"quarantined={counts.get('quarantined', 0)} "
        f"-> {paths['utterances']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
