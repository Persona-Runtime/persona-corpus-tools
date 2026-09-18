"""Dataset versioning.

`dataset_version` identifies *which processing produced this corpus*. It must be
deterministic — same inputs, same value — and it must change whenever anything
that shapes the output changes.

Why it matters: Qdrant is not backed up, it is regenerated from the raw files.
A regenerated collection is only comparable to the original if every input was
identical. `dataset_version` is the single value that says so, which means a
field it fails to cover is a silent hole in that guarantee.

Everything is hashed through canonical JSON rather than string concatenation, so
two different component sets can never collapse into the same material.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Bump when the *meaning* of the computation changes, not when inputs change.
VERSIONING_SCHEME = "ds1"


def _digest(payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_digest(output_affecting_fields: dict[str, str]) -> str:
    """Digest of the manifest fields that end up in output records.

    Sourced from `SourceManifest.output_affecting()` so the field list lives in
    one place: adding a field there automatically brings it into the version.
    """
    return _digest(output_affecting_fields)


def compute_dataset_version(
    *,
    parser_version: str,
    persona_config_digest: str,
    manifest_digest: str,
) -> str:
    """Version for one source.

    Covers the adapter's behaviour, the alias table that decides which lines are
    kept, and every manifest field that reaches an output record (including the
    source file's own hash).
    """
    return (
        f"{VERSIONING_SCHEME}-"
        + _digest(
            {
                "scheme": VERSIONING_SCHEME,
                "parser_version": parser_version,
                "persona_config_digest": persona_config_digest,
                "manifest_digest": manifest_digest,
            }
        )[:16]
    )


def compute_reviewed_smi_dataset_version(
    *, parser_version: str, persona_config_digest: str, manifest_digest: str, annotation_digest: str
) -> str:
    """Version a reviewed subtitle export; human labels are an input, not metadata."""
    return (
        f"{VERSIONING_SCHEME}-"
        + _digest(
            {
                "scheme": VERSIONING_SCHEME,
                "parser_version": parser_version,
                "persona_config_digest": persona_config_digest,
                "manifest_digest": manifest_digest,
                "annotation_digest": annotation_digest,
            }
        )[:16]
    )


def compute_corpus_version(source_versions: dict[str, str]) -> str:
    """Version for a whole run, derived from the per-source versions.

    Each utterance carries its own source's version so a record is
    self-describing. This one identifies the corpus as a set — adding,
    removing or changing any source changes it.
    """
    return f"{VERSIONING_SCHEME}corpus-" + _digest(source_versions)[:16]
