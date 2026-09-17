"""Manifest-driven ingestion.

Loop 1 took source identity from CLI flags, which made identity a property of
the command someone typed. Loop 2 takes it from ``source_manifest.yaml`` stored
beside the raw file, so identity and integrity travel with the data.

The run has two phases, and nothing is written during the first:

  **Phase 1 — validate everything.** Schema, path containment, retention,
  adapter support, and the SHA-256 of every file. The verified bytes are held
  in memory. A failure anywhere here ends the run having written nothing.

  **Phase 2 — process everything.** Parse into a temporary directory, then
  move it into place in one step.

The split matters: a run that fails halfway must not leave a partial corpus
next to a stale run report, because then nothing can say which run produced
what is on disk. The output directory is either the complete result of one
successful run, or untouched.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..adapters.errors import AdapterParseError
from ..adapters.registry import Adapter, UnsupportedSourceError, resolve_adapter
from ..canonical.models import RunReport, SourceMeta
from ..canonical.personas import ConfigError, PersonaConfig, load_persona_config
from ..canonical.versioning import (
    compute_corpus_version,
    compute_dataset_version,
    file_digest,
    manifest_digest,
)
from ..intake.manifest import (
    ManifestError,
    SourceManifest,
    check_retention,
    load_manifest,
    load_manifests,
    read_verified_bytes,
)
from ..reporting.writer import write_parquet, write_parse_result, write_run_report

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_GATE = 3
EXIT_UNSUPPORTED = 4
EXIT_PARSE = 5


class CliError(Exception):
    """A failure that should end the run with a specific exit code."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidatedSource:
    """One source that passed every gate, with its verified bytes in hand."""

    manifest: SourceManifest
    adapter: Adapter
    data: bytes
    meta: SourceMeta


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="persona-ingest",
        description="Parse locally held sources into canonical persona utterances.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", type=Path, help="a single source_manifest.yaml")
    group.add_argument("--manifest-dir", type=Path, help="directory of manifests")
    parser.add_argument(
        "--private-root",
        type=Path,
        required=True,
        help="root the manifest 'file' paths resolve against",
    )
    parser.add_argument("--personas", type=Path, required=True, help="persona alias config")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--parquet", action="store_true", help="also write Parquet")
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        help="override today's date for retention checks (testing)",
    )
    return parser


# --- Phase 1: validate ------------------------------------------------------


def validate_all(
    manifests: list[SourceManifest],
    private_root: Path,
    persona_config_digest: str,
    today: date,
) -> list[ValidatedSource]:
    """Run every gate over every source. Writes nothing."""
    validated: list[ValidatedSource] = []
    for manifest in manifests:
        try:
            path = manifest.resolve_file(private_root)
            check_retention(manifest, today)
        except ManifestError as exc:
            raise CliError(str(exc), EXIT_GATE) from exc

        try:
            adapter = resolve_adapter(manifest)
        except UnsupportedSourceError as exc:
            raise CliError(str(exc), EXIT_UNSUPPORTED) from exc

        try:
            data = read_verified_bytes(manifest, path)
        except ManifestError as exc:
            raise CliError(str(exc), EXIT_GATE) from exc

        dataset_version = compute_dataset_version(
            parser_version=adapter.parser_version,
            persona_config_digest=persona_config_digest,
            manifest_digest=manifest_digest(manifest.output_affecting()),
        )
        validated.append(
            ValidatedSource(
                manifest=manifest,
                adapter=adapter,
                data=data,
                meta=SourceMeta(
                    source_id=manifest.source_id,
                    character_id=manifest.character_id,
                    series=manifest.series,
                    episode=manifest.episode,
                    language=manifest.language,
                    sha256=manifest.sha256,
                    input_type=manifest.input_type,
                    dataset_version=dataset_version,
                ),
            )
        )
    return validated


# --- Phase 2: process into a temporary directory, then move -----------------


def assert_replaceable(out: Path) -> None:
    """Refuse to replace a directory that is not ours.

    The run ends by swapping this directory out. A typo in `--out` should not
    be able to destroy an unrelated folder.
    """
    if not out.exists():
        return
    if not out.is_dir():
        raise CliError(f"--out exists and is not a directory: {out}", EXIT_CONFIG)
    if any(out.iterdir()) and not (out / "run_report.json").is_file():
        raise CliError(
            f"--out is not empty and has no run_report.json, so it does not look like "
            f"an output directory: {out}. Refusing to replace it.",
            EXIT_CONFIG,
        )


def process_all(
    sources: list[ValidatedSource],
    personas: PersonaConfig,
    persona_config_digest: str,
    out: Path,
    write_parquet_too: bool,
) -> RunReport:
    staging = out.parent / f".{out.name}.tmp-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        report = _parse_into(sources, personas, persona_config_digest, staging, write_parquet_too)
        write_run_report(staging, report)
        _swap_into_place(staging, out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return report


def _parse_into(
    sources: list[ValidatedSource],
    personas: PersonaConfig,
    persona_config_digest: str,
    staging: Path,
    write_parquet_too: bool,
) -> RunReport:
    source_versions: dict[str, str] = {}
    rows: list[dict[str, object]] = []
    totals = {"sources": 0, "target": 0, "quarantined": 0}

    for source in sources:
        manifest = source.manifest
        try:
            result = source.adapter.parse(source.data, source.meta, personas, manifest.character_id)
        except AdapterParseError as exc:
            raise CliError(str(exc), EXIT_PARSE) from exc
        paths = write_parse_result(staging, result, manifest.source_id)
        if write_parquet_too:
            write_parquet(
                staging / "parsed" / f"{manifest.source_id}.parquet",
                [u.to_dict() for u in result.utterances],
            )

        counts = result.report.counts if result.report else {}
        source_versions[manifest.source_id] = source.meta.dataset_version
        totals["sources"] += 1
        totals["target"] += counts.get("target", 0)
        totals["quarantined"] += counts.get("quarantined", 0)

        rows.append(
            {
                "source_id": manifest.source_id,
                "character_id": manifest.character_id,
                "series": manifest.series,
                "episode": manifest.episode,
                "language": manifest.language,
                "input_type": manifest.input_type,
                "parser_profile": manifest.parser_profile,
                "parser_version": source.adapter.parser_version,
                "rights_status": manifest.rights_status,
                "delete_at": manifest.delete_at.isoformat() if manifest.delete_at else None,
                "source_sha256": manifest.sha256,
                "manifest_digest": manifest_digest(manifest.output_affecting()),
                "persona_config_digest": persona_config_digest,
                "dataset_version": source.meta.dataset_version,
                "target": counts.get("target", 0),
                "quarantined": counts.get("quarantined", 0),
            }
        )
        print(
            f"{manifest.source_id}: target={counts.get('target', 0)} "
            f"quarantined={counts.get('quarantined', 0)} -> {paths['utterances'].name}"
        )

    return RunReport(
        dataset_version=compute_corpus_version(source_versions),
        sources=rows,
        totals=totals,
    )


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


# --- entry point ------------------------------------------------------------


def _load_manifests(args: argparse.Namespace) -> list[SourceManifest]:
    manifests = (
        [load_manifest(args.manifest)] if args.manifest else load_manifests(args.manifest_dir)
    )
    if not manifests:
        raise CliError("no manifests found", EXIT_CONFIG)
    return manifests


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    today = args.today or date.today()

    try:
        try:
            personas = load_persona_config(args.personas)
        except (OSError, ConfigError) as exc:
            raise CliError(str(exc), EXIT_CONFIG) from exc

        persona_config_digest = file_digest(args.personas)

        try:
            manifests = _load_manifests(args)
        except (OSError, ManifestError) as exc:
            raise CliError(str(exc), EXIT_CONFIG) from exc

        assert_replaceable(args.out)
        sources = validate_all(manifests, args.private_root, persona_config_digest, today)
        report = process_all(sources, personas, persona_config_digest, args.out, args.parquet)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code

    print(
        f"corpus {report.dataset_version} "
        f"({report.totals['sources']} source(s)) -> {args.out / 'run_report.json'}"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
