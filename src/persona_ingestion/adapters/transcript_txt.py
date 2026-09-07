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

import re
from dataclasses import dataclass
from pathlib import Path

from ..canonical.models import (
    ParseReport,
    ParseResult,
    QuarantineRecord,
    SourceMeta,
    Utterance,
)
from ..canonical.normalize import merge_lines, normalize_text
from ..canonical.personas import PersonaConfig, SpeakerKind

PARSER_VERSION = "transcript-txt-v1"

# A header candidate is '[' followed by a digit; anything else starting with
# '[' (for example an on-screen sign) is treated as body text.
_HEADER_CANDIDATE_RE = re.compile(r"^\s*\[\s*\d")
_HEADER_RE = re.compile(r"^\s*\[\s*(?P<ts>\d{1,3}:\d{2}(?::\d{2})?)\s*\]\s*(?P<speaker>\S.*?)\s*$")


class ParseError(ValueError):
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

    blocks, quarantine = _split_blocks(text, meta.source_id, report)
    result.quarantine.extend(quarantine)

    for block in blocks:
        report.bump("blocks_total")
        report.speakers_seen[normalize_text(block.speaker_raw)] += 1

        body = merge_lines(block.body)
        if not body:
            report.bump("quarantined")
            result.quarantine.append(
                QuarantineRecord(
                    source_id=meta.source_id,
                    line_no=block.line_no,
                    reason="empty_body",
                    raw=f"[{block.timestamp}] {block.speaker_raw}",
                    parser_version=PARSER_VERSION,
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
            )
        )
        report.bump("target")

    return result


def parse_transcript_file(
    path: Path,
    meta: SourceMeta,
    personas: PersonaConfig,
    target_character_id: str,
) -> ParseResult:
    text = path.read_text(encoding="utf-8")
    return parse_transcript_txt(text, meta, personas, target_character_id)


def _split_blocks(
    text: str, source_id: str, report: ParseReport
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
                    QuarantineRecord(
                        source_id=source_id,
                        line_no=line_no,
                        reason="malformed_header",
                        raw=line.strip(),
                        parser_version=PARSER_VERSION,
                    )
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
                    QuarantineRecord(
                        source_id=source_id,
                        line_no=line_no,
                        reason=(
                            "orphaned_body_after_malformed_header"
                            if orphaned
                            else "text_before_first_header"
                        ),
                        raw=line.strip(),
                        parser_version=PARSER_VERSION,
                    )
                )
            continue

        current.body.append(line)

    if current is not None:
        blocks.append(current)

    return blocks, quarantine
