"""Markdown "paste" output — §4-1 of the corpus-split handoff.

Unlike the JSONL writers in ``writer.py``, this is not a private review
artifact: it is the text the user copies into the web app's textarea. So it
carries no source/attribution comments (those stay in the local manifest),
and tables are already flattened to plain paragraphs upstream by the wiki
extractor — there is no separate table-vs-prose choice to make here, the
extractor made it once for the whole pipeline.
"""

from __future__ import annotations

from typing import Any

_MAX_HEADING_LEVEL = 3


def render_wiki_markdown(documents: list[dict[str, Any]], paragraphs: list[dict[str, Any]]) -> str:
    """Render extracted wiki paragraphs as one pasteable markdown document.

    Documents are concatenated in the order given (the extractor's source_id
    order). Within a document, a heading is only emitted when the paragraph's
    heading_path diverges from the previous one — printing every ancestor
    again on every paragraph would not match "나무위키 문단 계층을 그대로".
    """
    footnote_numbers = _assign_footnote_numbers(paragraphs)
    by_document: dict[str, list[dict[str, Any]]] = {}
    for row in paragraphs:
        by_document.setdefault(str(row["document_id"]), []).append(row)

    lines: list[str] = []
    for document in documents:
        document_id = str(document["document_id"])
        rows = by_document.get(document_id, [])
        current_path: list[str] = []
        footnote_defs: list[tuple[int, str]] = []
        seen_footnote_ids: set[str] = set()
        for row in rows:
            if row["kind"] == "footnote":
                footnote_id = row.get("footnote_id")
                if isinstance(footnote_id, str) and footnote_id not in seen_footnote_ids:
                    seen_footnote_ids.add(footnote_id)
                    number = footnote_numbers.get(footnote_id)
                    if number is not None:
                        footnote_defs.append((number, str(row["text"])))
                continue
            heading_path = [str(part) for part in row["heading_path"]]
            current_path = _emit_heading_delta(lines, current_path, heading_path)
            refs = sorted(
                {footnote_numbers[ref] for ref in row["footnote_refs"] if ref in footnote_numbers}
            )
            markers = "".join(f"[^{n}]" for n in refs)
            lines.append(f"{row['text']}{markers}")
            lines.append("")
        if footnote_defs:
            lines.append("## 각주")
            lines.append("")
            for number, text in sorted(footnote_defs):
                lines.append(f"[^{number}]: {text}")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def _emit_heading_delta(
    lines: list[str], current_path: list[str], heading_path: list[str]
) -> list[str]:
    """Print only the headings that changed, at level = position (capped at ###)."""
    divergence = 0
    for a, b in zip(current_path, heading_path, strict=False):
        if a != b:
            break
        divergence += 1
    for index in range(divergence, len(heading_path)):
        level = min(index + 1, _MAX_HEADING_LEVEL)
        lines.append(f"{'#' * level} {heading_path[index]}")
        lines.append("")
    return heading_path


def _assign_footnote_numbers(paragraphs: list[dict[str, Any]]) -> dict[str, int]:
    """Number footnotes by first citation order, then any never-cited leftovers."""
    numbers: dict[str, int] = {}
    for row in paragraphs:
        if row["kind"] == "footnote":
            continue
        for ref in row["footnote_refs"]:
            if ref not in numbers:
                numbers[ref] = len(numbers) + 1
    for row in paragraphs:
        if row["kind"] != "footnote":
            continue
        footnote_id = row.get("footnote_id")
        if isinstance(footnote_id, str) and footnote_id not in numbers:
            numbers[footnote_id] = len(numbers) + 1
    return numbers


def render_speech_lines(entries: list[tuple[str, str]]) -> str:
    """Render ``(speaker, text)`` pairs as the §4-1 speech paste format.

    One line per entry: ``화자: 대사``. This pipeline does not currently carry
    a situation/상황 note per utterance, so the ``화자 (상황): 대사`` variant
    is not produced here — add it once that metadata exists upstream.
    """
    return "".join(f"{speaker}: {text}\n" for speaker, text in entries)
