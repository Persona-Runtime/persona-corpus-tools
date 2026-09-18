"""`source_manifest.yaml` intake.

Loop 1 took source identity from CLI flags, which made identity a property of
the command someone typed. Loop 2 takes it from a manifest stored beside the raw
file, so identity and integrity travel *with the data*.

Three gates run before any parsing happens:

  1. **Schema** — required fields present and well-formed
  2. **Integrity** — the file's SHA-256 matches what the manifest declared
  3. **Retention** — `delete_at` has not passed

Each is fatal. Processing a file whose content changed since registration, or
whose retention window closed, produces a corpus that cannot be defended later.

`rights_status` is recorded, not enforced: it lands in the run report so the
provenance of a corpus is inspectable, but it does not gate processing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

VALID_INPUT_TYPES = frozenset({"transcript_txt", "script_pdf", "subtitle_smi"})
VALID_RIGHTS_STATUS = frozenset({"synthetic", "licensed", "private_unverified"})

_REQUIRED_FIELDS = (
    "source_id",
    "character_id",
    "series",
    "episode",
    "input_type",
    "language",
    "rights_status",
    "file",
    "sha256",
    "parser_profile",
)

# Fields that change what ends up in an output record. Any change here must
# change dataset_version, so this tuple is the single source of that truth.
OUTPUT_AFFECTING_FIELDS = (
    "character_id",
    "series",
    "episode",
    "language",
    "input_type",
    "parser_profile",
    "sha256",
)


class ManifestError(ValueError):
    """The manifest itself is unusable."""


class IntegrityError(ManifestError):
    """The file on disk does not match what the manifest declared."""


class RetentionError(ManifestError):
    """The retention window for this source has closed."""


@dataclass(frozen=True)
class SourceManifest:
    source_id: str
    character_id: str
    series: str
    episode: str
    input_type: str
    language: str
    rights_status: str
    file: str
    sha256: str
    parser_profile: str
    acquired_at: date | None = None
    delete_at: date | None = None

    def resolve_file(self, private_root: Path) -> Path:
        """Resolve `file` against the private root, refusing to escape it.

        A manifest is data. A `file` of `../../etc/passwd` must not be able to
        point the pipeline outside the directory the operator granted.
        """
        root = private_root.resolve()
        candidate = (root / self.file).resolve()
        if not candidate.is_relative_to(root):
            raise ManifestError(f"{self.source_id}: 'file' escapes the private root: {self.file!r}")
        return candidate

    def output_affecting(self) -> dict[str, str]:
        """The subset of the manifest that shapes output records."""
        return {name: str(getattr(self, name)) for name in OUTPUT_AFFECTING_FIELDS}


def _as_date(value: Any, field: str, source_id: str) -> date | None:
    """Accept a YAML date or an ISO string; reject datetime explicitly.

    YAML turns an unquoted `2027-08-30` into a `date` but a quoted
    `"2027-08-30"` into a `str`. Both are normal ways to write a date, so both
    are accepted.

    `datetime` is a subclass of `date`, so an isinstance check alone would let
    a timestamp through and then raise TypeError later when comparing it to a
    date. It is rejected here, where the message can say why.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ManifestError(
            f"{source_id}: '{field}' must be a date, not a timestamp: {value!r}. Use YYYY-MM-DD."
        )
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ManifestError(
                f"{source_id}: '{field}' is not a valid ISO date: {value!r}"
            ) from exc
    raise ManifestError(f"{source_id}: '{field}' must be a date (YYYY-MM-DD), got {value!r}")


def load_manifest(path: Path) -> SourceManifest:
    import yaml

    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ManifestError(f"{path}: top level must be a mapping")

    source_id = raw.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ManifestError(f"{path}: 'source_id' is required and must be a non-empty string")

    missing = [f for f in _REQUIRED_FIELDS if not str(raw.get(f, "")).strip()]
    if missing:
        raise ManifestError(f"{source_id}: missing required field(s): {', '.join(missing)}")

    input_type = str(raw["input_type"])
    if input_type not in VALID_INPUT_TYPES:
        raise ManifestError(
            f"{source_id}: input_type {input_type!r} not in {sorted(VALID_INPUT_TYPES)}"
        )

    rights_status = str(raw["rights_status"])
    if rights_status not in VALID_RIGHTS_STATUS:
        raise ManifestError(
            f"{source_id}: rights_status {rights_status!r} not in {sorted(VALID_RIGHTS_STATUS)}"
        )

    sha = str(raw["sha256"]).lower()
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise ManifestError(f"{source_id}: 'sha256' must be 64 hex characters")

    return SourceManifest(
        source_id=source_id,
        character_id=str(raw["character_id"]),
        series=str(raw["series"]),
        episode=str(raw["episode"]),
        input_type=input_type,
        language=str(raw["language"]),
        rights_status=rights_status,
        file=str(raw["file"]),
        sha256=sha,
        parser_profile=str(raw["parser_profile"]),
        acquired_at=_as_date(raw.get("acquired_at"), "acquired_at", source_id),
        delete_at=_as_date(raw.get("delete_at"), "delete_at", source_id),
    )


def load_manifests(directory: Path) -> list[SourceManifest]:
    """Load every manifest in a directory, ordered by source_id for determinism."""
    paths = sorted(list(directory.glob("*.yaml")) + list(directory.glob("*.yml")))
    manifests = [load_manifest(p) for p in paths]
    seen: dict[str, Path] = {}
    for manifest, path in zip(manifests, paths, strict=True):
        if manifest.source_id in seen:
            raise ManifestError(
                f"duplicate source_id {manifest.source_id!r} in "
                f"{seen[manifest.source_id]} and {path}"
            )
        seen[manifest.source_id] = path
    return sorted(manifests, key=lambda m: m.source_id)


def read_verified_bytes(manifest: SourceManifest, path: Path) -> bytes:
    """Read the file once and return it only if it matches the declared hash.

    Reading once and passing the bytes onward closes the gap between "we
    checked the file" and "we parsed the file" — there is no second open in
    which the content could differ.
    """
    import hashlib

    if not path.is_file():
        raise IntegrityError(f"{manifest.source_id}: file not found: {path}")
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != manifest.sha256:
        raise IntegrityError(
            f"{manifest.source_id}: sha256 mismatch\n"
            f"  manifest: {manifest.sha256}\n"
            f"  actual:   {actual}\n"
            "The file changed since it was registered. Re-register it deliberately "
            "rather than editing the manifest to match."
        )
    return data


def check_retention(manifest: SourceManifest, today: date) -> None:
    """Refuse to process a source whose retention window has closed."""
    if manifest.delete_at is not None and today > manifest.delete_at:
        raise RetentionError(
            f"{manifest.source_id}: retention expired on {manifest.delete_at.isoformat()} "
            f"(today is {today.isoformat()}). Delete the source instead of processing it."
        )
