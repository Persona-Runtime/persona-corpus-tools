from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from ..canonical.versioning import manifest_digest
from ..intake.manifest import check_retention, load_manifest, read_verified_bytes
from .parser import PARSER_VERSION, parse_smi
from .storage import write_prepared


def prepare(manifest_path: Path, private_root: Path, out: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    if manifest.input_type != "subtitle_smi":
        raise ValueError("manifest input_type must be subtitle_smi")
    check_retention(manifest, date.today())
    rows = [
        item.to_dict()
        for item in parse_smi(read_verified_bytes(manifest, manifest.resolve_file(private_root)))
    ]
    metadata: dict[str, Any] = {
        "schema_version": "1",
        "parser_version": PARSER_VERSION,
        "source_id": manifest.source_id,
        "source_sha256": manifest.sha256,
        "manifest_digest": manifest_digest(manifest.output_affecting()),
        "character_id": manifest.character_id,
        "series": manifest.series,
        "episode": manifest.episode,
        "language": manifest.language,
        "input_type": manifest.input_type,
    }
    write_prepared(out, metadata, rows)
    return metadata
