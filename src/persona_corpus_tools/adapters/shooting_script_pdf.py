"""Adapter for text-based shooting-script PDFs.

The PDF is deliberately converted with Poppler's layout-preserving mode.  A
shooting script communicates its structure through horizontal position: cue
lines are farther right than dialogue, while action is at the left margin.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..canonical.models import ParseReport, ParseResult, QuarantineRecord, SourceMeta, Utterance
from ..canonical.normalize import merge_lines, normalize_text
from ..canonical.personas import PersonaConfig, SpeakerKind
from .errors import AdapterParseError

PARSER_VERSION = "shooting-script-pdf-v1"

# These bands match the verified Sherlock shooting-script layout.  They are
# intentionally part of this profile rather than a generic PDF heuristic.
_CUE_INDENT = 20
_DIALOGUE_INDENT = 8
_CUE_NAME_RE = r"[A-Za-z][A-Za-z0-9 .\-'\u2019]*?"
_CUE_RE = re.compile(rf"^(?P<name>{_CUE_NAME_RE})(?:\s+\((?P<qualifier>[^)]*)\))?$")
_JOINT_CUE_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9 .\-'\u2019]*(?:/[A-Za-z][A-Za-z0-9 .\-'\u2019]*)+$"
)
_ALLOWED_QUALIFIERS = frozenset({"v.o.", "v.o", "vo", "cont'd", "cont\u2019d"})


@dataclass
class _OpenUtterance:
    cue: str
    page: int
    page_line: int
    seq: int
    lines: list[str]


def parse_shooting_script_pdf_bytes(
    data: bytes,
    meta: SourceMeta,
    personas: PersonaConfig,
    target_character_id: str,
) -> ParseResult:
    """Extract validated PDF bytes once, then parse the resulting layout text."""
    layout = extract_layout_text(data, meta.source_id)
    return parse_shooting_script_layout(layout, meta, personas, target_character_id)


def extract_layout_text(data: bytes, source_id: str) -> str:
    """Run the fixed Poppler command against a short-lived copy of the bytes."""
    with tempfile.TemporaryDirectory(prefix="persona-ingest-") as directory:
        input_path = Path(directory) / "source.pdf"
        input_path.write_bytes(data)
        try:
            completed = subprocess.run(
                ["pdftotext", "-layout", "-enc", "UTF-8", str(input_path), "-"],
                check=False,
                capture_output=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdapterParseError(f"{source_id}: PDF text extraction failed: {exc}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise AdapterParseError(f"{source_id}: pdftotext failed: {detail or 'unknown error'}")
    try:
        text = completed.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdapterParseError(f"{source_id}: pdftotext did not produce UTF-8 text") from exc
    if not text.strip():
        raise AdapterParseError(f"{source_id}: PDF has no extractable text; OCR is not supported")
    return text


def parse_shooting_script_layout(
    text: str,
    meta: SourceMeta,
    personas: PersonaConfig,
    target_character_id: str,
) -> ParseResult:
    """Parse Poppler ``-layout`` output into target-character utterances."""
    if target_character_id not in personas.persona_ids:
        raise AdapterParseError(f"unknown target persona {target_character_id!r}")

    report = ParseReport(
        source_id=meta.source_id,
        character_id=target_character_id,
        parser_version=PARSER_VERSION,
    )
    result = ParseResult(report=report)
    current: _OpenUtterance | None = None
    more_pending = False
    cue_seq = 0
    valid_cues = 0

    def quarantine(page: int, page_line: int, reason: str, raw: str) -> None:
        report.bump("quarantined")
        result.quarantine.append(
            QuarantineRecord(
                source_id=meta.source_id,
                # Existing quarantine schema has one line field.  This stable
                # composite remains searchable while utterances retain the
                # precise page/page_line locator required downstream.
                line_no=page * 10000 + page_line,
                reason=reason,
                raw=normalize_text(raw),
                parser_version=PARSER_VERSION,
                source_sha256=meta.sha256,
                dataset_version=meta.dataset_version,
            )
        )

    def close_current() -> None:
        nonlocal current, more_pending
        if current is None:
            return
        body = merge_lines(current.lines)
        if not body:
            quarantine(current.page, current.page_line, "empty_dialogue", current.cue)
        else:
            result.utterances.append(
                Utterance(
                    utterance_id=(f"{meta.source_id}-p{current.page:03d}-{current.seq:04d}"),
                    character_id=target_character_id,
                    series=meta.series,
                    episode=meta.episode,
                    source_id=meta.source_id,
                    source_locator={
                        "page": current.page,
                        "page_line": current.page_line,
                        "cue": current.cue,
                    },
                    speaker_raw=current.cue,
                    speaker_normalized=target_character_id,
                    language=meta.language,
                    text=body,
                    parser_version=PARSER_VERSION,
                    source_sha256=meta.sha256,
                    dataset_version=meta.dataset_version,
                )
            )
            report.bump("target")
        current = None
        more_pending = False

    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()

    for page, page_text in enumerate(pages, start=1):
        report.bump("pages")
        for page_line, raw in enumerate(page_text.splitlines(), start=1):
            report.bump("input_lines")
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped:
                continue

            indent = len(line) - len(line.lstrip())
            if stripped.casefold() == "(more)":
                if current is not None:
                    more_pending = True
                    report.bump("more_markers")
                continue
            if stripped.startswith("(") and stripped.endswith(")"):
                if current is not None:
                    report.bump("delivery_directions_excluded")
                continue

            if indent >= _CUE_INDENT:
                cue = _parse_cue(stripped)
                if cue is not None:
                    cue_seq += 1
                    valid_cues += 1
                    resolution = personas.resolve(cue)
                    report.speakers_seen[normalize_text(cue)] += 1
                    continues_current = (
                        current is not None
                        and more_pending
                        and _is_continuation_cue(cue)
                        and resolution.kind is SpeakerKind.PERSONA
                        and resolution.persona_id == target_character_id
                    )
                    if continues_current:
                        more_pending = False
                    else:
                        close_current()
                    if (
                        not continues_current
                        and resolution.kind is SpeakerKind.PERSONA
                        and resolution.persona_id == target_character_id
                    ):
                        current = _OpenUtterance(cue, page, page_line, cue_seq, [])
                    elif not continues_current and resolution.kind is SpeakerKind.PERSONA:
                        report.bump("excluded_other_persona")
                    elif resolution.kind is SpeakerKind.NON_DIALOGUE:
                        report.bump("excluded_non_dialogue")
                    else:
                        report.bump("excluded_unknown_speaker")
                    continue
                if _JOINT_CUE_RE.fullmatch(stripped):
                    close_current()
                    cue_seq += 1
                    valid_cues += 1
                    report.bump("excluded_joint_cue")
                    continue
                if _looks_like_cue(stripped):
                    if _is_scene_transition(stripped):
                        close_current()
                        report.bump("excluded_scene_direction")
                        continue
                    if _is_page_folio(stripped):
                        report.bump("excluded_page_folio")
                        continue
                    close_current()
                    quarantine(page, page_line, "ambiguous_cue", line)
                    continue

            if current is not None and _DIALOGUE_INDENT <= indent < _CUE_INDENT:
                current.lines.append(line)
            elif indent < _DIALOGUE_INDENT:
                if more_pending:
                    report.bump("page_layout_excluded")
                else:
                    close_current()

        # A form-feed alone is not a continuation marker.  Leave an utterance
        # open only when the screenplay explicitly uses ``(MORE)``.
        if not more_pending:
            close_current()

    close_current()
    if valid_cues == 0:
        raise AdapterParseError(f"{meta.source_id}: PDF layout contains no screenplay speaker cues")
    if not result.utterances:
        raise AdapterParseError(
            f"{meta.source_id}: PDF contains no utterances for {target_character_id}"
        )
    return result


def _parse_cue(value: str) -> str | None:
    match = _CUE_RE.fullmatch(value)
    if match is None:
        return None
    name = normalize_text(match.group("name"))
    qualifier = match.group("qualifier")
    if name != name.upper():
        return None
    if qualifier is not None and normalize_text(qualifier).casefold() not in _ALLOWED_QUALIFIERS:
        return None
    return normalize_text(value)


def _looks_like_cue(value: str) -> bool:
    """Reject malformed all-caps cue-band text instead of treating it as dialogue."""
    name = value.split("(", maxsplit=1)[0].strip()
    return any(char.isalpha() for char in name) and name == name.upper()


def _is_continuation_cue(value: str) -> bool:
    match = _CUE_RE.fullmatch(value)
    return (
        match is not None
        and match.group("qualifier") is not None
        and normalize_text(match.group("qualifier")).casefold() in {"cont'd", "cont\u2019d"}
    )


def _is_scene_transition(value: str) -> bool:
    return value.endswith(":")


def _is_page_folio(value: str) -> bool:
    return re.fullmatch(r"\d+[A-Z]?\.?", value) is not None
