"""Adapter for timestamped, speaker-labelled transcript text files.

Expected block shape::

    [MM:SS] Speaker Name
    first physical line
    second physical line

A block runs from its header to the next header or end of file. Blank lines
inside a block are separators, not terminators.

Design rules:
  * a line that cannot be interpreted is quarantined with a reason, never dropped
  * a speaker is resolved through the persona alias table, never guessed
  * blocks are numbered across the whole file, so utterance ids stay stable
    even when the target persona changes
  * a malformed header closes the current block. Its orphaned body lines are
    quarantined rather than attributed to the previous speaker, because
    guessing the speaker there would be silent leakage.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from ..canonical.models import (
    ParseReport,
    ParseResult,
    QuarantineRecord,
    SourceMeta,
    Utterance,
)
from ..canonical.normalize import merge_lines, normalize_text
from ..canonical.personas import PersonaConfig, SpeakerKind
from .errors import AdapterParseError

#: This adapter's own version. Bump when its behaviour changes.
#: It is deliberately not shared with other adapters — a fix here must not
#: change the dataset version of a corpus produced by the PDF adapter.
PARSER_VERSION = "transcript-txt-v2"

# A header candidate is '[' followed by a digit; anything else starting with
# '[' (for example an on-screen sign) is treated as body text.
_HEADER_CANDIDATE_RE = re.compile(r"^\s*\[\s*\d")
_HEADER_RE = re.compile(r"^\s*\[\s*(?P<ts>\d{1,3}:\d{2}(?::\d{2})?)\s*\]\s*(?P<speaker>\S.*?)\s*$")
_HTML_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_BOLDED_HEADER_RE = re.compile(r"^(\s*)\*\*(\[\s*\d.*)\*\*(\s*)$", re.MULTILINE)


class ParseError(AdapterParseError):
    """Raised when the input cannot be parsed at all."""


@dataclass(frozen=True)
class _Block:
    seq: int
    line_no: int
    timestamp: str
    speaker_raw: str
    body: list[str]


def timestamp_to_seconds(value: str) -> int:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


def parse_transcript_bytes(
    data: bytes,
    meta: SourceMeta,
    personas: PersonaConfig,
    target_character_id: str,
) -> ParseResult:
    """Entry point used by the pipeline.

    Takes the bytes that were hash-verified rather than a path, so the file is
    read exactly once and cannot change between verification and parsing.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseError(f"{meta.source_id}: not valid UTF-8 text: {exc}") from exc
    return parse_transcript_txt(
        _normalize_archived_markdown(text), meta, personas, target_character_id
    )


def _normalize_archived_markdown(text: str) -> str:
    """Make an archive-exported transcript look like physical text lines.

    SubArchivist Markdown exports use literal ``<br>`` tags for transcript
    line breaks and wrap timestamp headers in Markdown bold markers. This is
    presentation markup, not dialogue, so remove only those two forms before
    block parsing. HTML entities are decoded so indentation does not leak into
    the canonical text.

    Deliberately do *not* strip general Markdown: asterisks or brackets inside
    a dialogue line are content and must survive unchanged.
    """
    with_lines = _HTML_BREAK_RE.sub("\n", text)
    without_header_bold = _BOLDED_HEADER_RE.sub(r"\1\2\3", with_lines)
    return html.unescape(without_header_bold)


def parse_transcript_txt(
    text: str,
    meta: SourceMeta,
    personas: PersonaConfig,
    target_character_id: str,
) -> ParseResult:
    """Parse transcript text and emit only the target persona's utterances."""
    if target_character_id not in personas.persona_ids:
        raise ParseError(f"unknown target persona {target_character_id!r}")

    report = ParseReport(
        source_id=meta.source_id,
        character_id=target_character_id,
        parser_version=PARSER_VERSION,
    )
    result = ParseResult(report=report)

    blocks, quarantine = _split_blocks(text, meta, report)
    result.quarantine.extend(quarantine)

    for block in blocks:
        report.bump("blocks_total")
        report.speakers_seen[normalize_text(block.speaker_raw)] += 1

        body = merge_lines(block.body)
        if not body:
            report.bump("quarantined")
            result.quarantine.append(
                _quarantine(
                    meta,
                    line_no=block.line_no,
                    reason="empty_body",
                    raw=f"[{block.timestamp}] {block.speaker_raw}",
                )
            )
            continue

        resolution = personas.resolve(block.speaker_raw)
        if resolution.kind is SpeakerKind.NON_DIALOGUE:
            report.bump("excluded_non_dialogue")
            continue
        if resolution.kind is SpeakerKind.UNKNOWN:
            report.bump("excluded_unknown_speaker")
            continue
        if resolution.persona_id != target_character_id:
            report.bump("excluded_other_persona")
            continue

        seconds = timestamp_to_seconds(block.timestamp)
        result.utterances.append(
            Utterance(
                utterance_id=f"{meta.source_id}-t{seconds:05d}-{block.seq:04d}",
                character_id=target_character_id,
                series=meta.series,
                episode=meta.episode,
                source_id=meta.source_id,
                source_locator={
                    "timestamp": block.timestamp,
                    "timestamp_seconds": seconds,
                    "line": block.line_no,
                },
                speaker_raw=normalize_text(block.speaker_raw),
                speaker_normalized=target_character_id,
                language=meta.language,
                text=body,
                parser_version=PARSER_VERSION,
                source_sha256=meta.sha256,
                dataset_version=meta.dataset_version,
            )
        )
        report.bump("target")

    return result


def _quarantine(meta: SourceMeta, *, line_no: int, reason: str, raw: str) -> QuarantineRecord:
    return QuarantineRecord(
        source_id=meta.source_id,
        line_no=line_no,
        reason=reason,
        raw=raw,
        parser_version=PARSER_VERSION,
        source_sha256=meta.sha256,
        dataset_version=meta.dataset_version,
    )


def _split_blocks(
    text: str, meta: SourceMeta, report: ParseReport
) -> tuple[list[_Block], list[QuarantineRecord]]:
    blocks: list[_Block] = []
    quarantine: list[QuarantineRecord] = []
    current: _Block | None = None
    orphaned = False
    seq = 0

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        report.bump("input_lines")
        line = raw_line.rstrip()

        if _HEADER_CANDIDATE_RE.match(line):
            match = _HEADER_RE.match(line)
            if match is None:
                # The block this header would have opened is unattributable, and
                # so is everything that follows it until the next valid header.
                if current is not None:
                    blocks.append(current)
                    current = None
                orphaned = True
                report.bump("quarantined")
                quarantine.append(
                    _quarantine(meta, line_no=line_no, reason="malformed_header", raw=line.strip())
                )
                continue
            if current is not None:
                blocks.append(current)
            orphaned = False
            seq += 1
            current = _Block(
                seq=seq,
                line_no=line_no,
                timestamp=match.group("ts"),
                speaker_raw=match.group("speaker"),
                body=[],
            )
            continue

        if current is None:
            if line.strip():
                report.bump("quarantined")
                quarantine.append(
                    _quarantine(
                        meta,
                        line_no=line_no,
                        reason=(
                            "orphaned_body_after_malformed_header"
                            if orphaned
                            else "text_before_first_header"
                        ),
                        raw=line.strip(),
                    )
                )
            continue

        current.body.append(line)

    if current is not None:
        blocks.append(current)

    return blocks, quarantine
