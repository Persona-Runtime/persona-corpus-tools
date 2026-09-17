"""Adapter registry.

`parser_profile` in the manifest selects the adapter. It names a *family*
(`transcript-txt`), not an exact version: the adapter reports its own version,
and that version is part of `dataset_version`. So upgrading a parser does not
require editing every manifest, but it does change the dataset version, which
is how the change becomes visible.

Each adapter carries its own version. A fix to the transcript parser must not
change the dataset version of a corpus produced by the PDF adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..canonical.models import ParseResult, SourceMeta
from ..canonical.personas import PersonaConfig
from ..intake.manifest import ManifestError, SourceManifest
from .shooting_script_pdf import PARSER_VERSION as SHOOTING_SCRIPT_PDF_VERSION
from .shooting_script_pdf import parse_shooting_script_pdf_bytes
from .transcript_txt import PARSER_VERSION as TRANSCRIPT_TXT_VERSION
from .transcript_txt import parse_transcript_bytes

ParseFn = Callable[[bytes, SourceMeta, PersonaConfig, str], ParseResult]


class UnsupportedSourceError(ManifestError):
    """No adapter implements this manifest's declared format."""


@dataclass(frozen=True)
class Adapter:
    parser_profile: str
    parser_version: str
    input_type: str
    parse: ParseFn


ADAPTERS: dict[str, Adapter] = {
    adapter.parser_profile: adapter
    for adapter in (
        Adapter(
            parser_profile="transcript-txt",
            parser_version=TRANSCRIPT_TXT_VERSION,
            input_type="transcript_txt",
            parse=parse_transcript_bytes,
        ),
        Adapter(
            parser_profile="shooting-script-pdf",
            parser_version=SHOOTING_SCRIPT_PDF_VERSION,
            input_type="script_pdf",
            parse=parse_shooting_script_pdf_bytes,
        ),
    )
}


def resolve_adapter(manifest: SourceManifest) -> Adapter:
    """Pick the adapter, refusing anything ambiguous.

    Two failure modes are distinguished on purpose:
      * an unimplemented format is reported as such (script_pdf today)
      * a manifest whose `input_type` and `parser_profile` disagree is a
        mistake in the manifest, not a missing feature
    """
    adapter = ADAPTERS.get(manifest.parser_profile)
    if adapter is None:
        raise UnsupportedSourceError(
            f"{manifest.source_id}: no adapter for parser_profile "
            f"{manifest.parser_profile!r} (implemented: {sorted(ADAPTERS)})"
        )
    if adapter.input_type != manifest.input_type:
        raise UnsupportedSourceError(
            f"{manifest.source_id}: parser_profile {manifest.parser_profile!r} handles "
            f"input_type {adapter.input_type!r}, but the manifest declares "
            f"{manifest.input_type!r}"
        )
    return adapter
