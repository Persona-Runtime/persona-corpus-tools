"""Small, conservative SAMI parser used before human speaker review."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

PARSER_VERSION = "subtitle-smi-v1"
_SYNC = re.compile(r"<sync\b[^>]*\bstart\s*=\s*[\"']?(\d+)[^>]*>", re.I)
_BR = re.compile(r"<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_HANGUL = re.compile(r"[가-힣]")


@dataclass(frozen=True)
class Subtitle:
    subtitle_id: str
    start_ms: int
    end_ms: int | None
    source_line: int
    source_char: int
    source_byte: int
    visible_text: str
    korean_text: str
    is_clear: bool
    timing_issue: str | None

    def to_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


def parse_smi(data: bytes) -> list[Subtitle]:
    """Decode CP949 and retain every SYNC block, including blank clear cues."""
    try:
        source = data.decode("cp949")
    except UnicodeDecodeError as exc:
        raise ValueError("SMI must be CP949-decodable") from exc
    matches = list(_SYNC.finditer(source))
    if not matches:
        raise ValueError("SMI contains no SYNC Start blocks")
    subtitles: list[Subtitle] = []
    for index, match in enumerate(matches, 1):
        next_match = matches[index] if index < len(matches) else None
        start = int(match.group(1))
        next_start = int(next_match.group(1)) if next_match else None
        end = next_start if next_start is not None and next_start >= start else None
        issue = (
            None if end is not None else ("missing_end" if next_start is None else "time_reversed")
        )
        block = source[match.end() : next_match.start() if next_match else len(source)]
        visible = _visible(block)
        korean = "\n".join(line for line in visible.splitlines() if _HANGUL.search(line))
        subtitles.append(
            Subtitle(
                subtitle_id=f"s{index:06d}",
                start_ms=start,
                end_ms=end,
                source_line=source.count("\n", 0, match.start()) + 1,
                source_char=match.start(),
                source_byte=len(source[: match.start()].encode("cp949")),
                visible_text=visible,
                korean_text=korean,
                is_clear=not bool(visible),
                timing_issue=issue,
            )
        )
    return subtitles


def _visible(block: str) -> str:
    value = _BR.sub("\n", block)
    value = _TAG.sub("", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)
