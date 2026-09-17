"""Deterministic HTML-to-paragraph extraction for the Tanjiro wiki acquisition."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ..reporting.writer import write_json, write_jsonl

PARSER_VERSION = "wiki-html-v4"
_IGNORED_TAGS = frozenset({"script", "style", "noscript", "img", "pre", "code", "svg"})
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "command",
        "embed",
        "hr",
        "img",
        "input",
        "keygen",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_BLOCK_TAGS = frozenset({"p", "li", "td", "th", "blockquote", "tr"})
_HEADING_TAGS = frozenset({"h2", "h3", "h4", "h5", "h6"})
_HEADING_BOUNDARY_TAGS = (
    _HEADING_TAGS
    | _BLOCK_TAGS
    | frozenset({"article", "div", "section", "table", "ul", "ol", "dl"})
)
_REVIEW_PATTERNS = (
    ("wiki_link_residue", "[["),
    ("template_residue", "{{{"),
    ("wiki_directive", "#!"),
)


class ExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class AcquiredSource:
    source_id: str
    title: str
    file: str
    language: str
    sha256: str


@dataclass
class _FootnoteContext:
    tag: str
    depth: int
    container_id: str | None
    candidate_ids: set[str]
    rows: list[dict[str, Any]]


class _ArticleParser(HTMLParser):
    """Extract article text and reject structurally incomplete acquired HTML."""

    def __init__(self, source: AcquiredSource) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.in_article = False
        self.article_seen = False
        self.article_closed = False
        self.article_depth: int | None = None
        self.tags: list[str] = []
        self.structure_errors: list[str] = []
        self.skip_roots: list[tuple[str, int]] = []
        self.toc_roots: list[tuple[str, int]] = []
        self.footnote_roots: list[_FootnoteContext] = []
        self.completed_footnotes: list[_FootnoteContext] = []
        self.heading_level: int | None = None
        self.heading_depth: int | None = None
        self.heading_parts: list[str] = []
        self.path: list[str] = []
        self.started = False
        self.parts: list[str] = []
        self.refs: list[str] = []
        self.line: int | None = None
        self.column: int | None = None
        self.rows: list[dict[str, Any]] = []
        self.sequence = 0
        self.br_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if self.in_article and self.heading_level is not None and tag in _HEADING_BOUNDARY_TAGS:
            self._finish_heading()
        if tag not in _VOID_TAGS:
            self.tags.append(tag)
        if tag == "article":
            if self.article_seen:
                self.structure_errors.append("multiple <article> elements")
                return
            self.article_seen = True
            self.in_article = True
            self.article_depth = len(self.tags)
            return
        if not self.in_article:
            return

        classes = set((attributes.get("class") or "").split())
        if self.skip_roots:
            return
        if tag in _IGNORED_TAGS:
            self.flush()
            if tag not in _VOID_TAGS:
                self.skip_roots.append((tag, len(self.tags)))
            return
        if "toc" in classes or "toc-item" in classes or tag == "nav":
            self.flush()
            if tag not in _VOID_TAGS:
                self.toc_roots.append((tag, len(self.tags)))
            return
        if self.toc_roots:
            return
        if "footnote-list" in classes:
            self.flush()
            if tag not in _VOID_TAGS:
                self.footnote_roots.append(
                    _FootnoteContext(
                        tag=tag,
                        depth=len(self.tags),
                        container_id=attributes.get("id"),
                        candidate_ids=set(),
                        rows=[],
                    )
                )
            return
        if self.footnote_roots:
            self._register_footnote_candidate(tag, classes, attributes.get("id"))
            if tag == "br":
                self._handle_break()
            return
        if "wikiFootnote" in classes:
            ref = (attributes.get("href") or "").removeprefix("#")
            if ref:
                self.refs.append(ref)
            if tag not in _VOID_TAGS:
                self.skip_roots.append((tag, len(self.tags)))
            return
        if tag in _HEADING_TAGS:
            self.flush()
            self.heading_level = int(tag[1])
            self.heading_depth = len(self.tags)
            self.heading_parts = []
            return
        if tag == "br":
            self._handle_break()
            return
        if tag in _BLOCK_TAGS:
            self.flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        try:
            index = len(self.tags) - 1 - self.tags[::-1].index(tag)
        except ValueError:
            return
        if tag == "article" and self.in_article and self.article_depth == index + 1:
            self._unwind_to(index)
            self._close_semantic_tag(tag, len(self.tags))
            self.in_article = False
            self.article_closed = True
            self.article_depth = None
            del self.tags[index:]
            return
        # namu.moe's page chrome has a few browser-tolerated stray end tags.
        # Treat omitted inner ends as implicitly closed, while still requiring
        # the acquired article and enclosing document to have explicit ends.
        self._unwind_to(index)
        self._close_semantic_tag(tag, len(self.tags))
        self.tags.pop()

    def handle_data(self, data: str) -> None:
        if not self.in_article or self.skip_roots or self.toc_roots:
            return
        if self.heading_level is not None:
            self.heading_parts.append(data)
            return
        if not self.started or not data.strip():
            return
        if self.line is None:
            self.line, self.column = self.getpos()
        self.parts.append(data)
        self.br_count = 0

    def flush(self) -> None:
        text = _normalise("".join(self.parts))
        if text and self.started:
            self.sequence += 1
            reasons = [name for name, marker in _REVIEW_PATTERNS if marker in text]
            self.rows.append(
                {
                    "paragraph_id": f"{self.source.source_id}-p{self.sequence:05d}",
                    "document_id": self.source.source_id,
                    "kind": "footnote" if self.footnote_roots else "body",
                    "text": text,
                    "heading_path": list(self.path),
                    "source_file": self.source.file,
                    "source_sha256": self.source.sha256,
                    "source_line": self.line,
                    "source_column": self.column,
                    "dom_ordinal": self.sequence,
                    "footnote_refs": sorted(set(self.refs)),
                    "footnote_id": None,
                    "extraction_status": "review_required" if reasons else "extracted",
                    "review_reasons": reasons,
                }
            )
            if self.footnote_roots:
                self.footnote_roots[-1].rows.append(self.rows[-1])
        self.parts = []
        self.refs = []
        self.line = None
        self.column = None
        self.br_count = 0

    def validate(self) -> None:
        if not self.article_seen:
            raise ExtractionError(f"{self.source.source_id}: missing <article> element")
        if not self.article_closed:
            raise ExtractionError(f"{self.source.source_id}: unclosed <article> element")
        if self.tags:
            raise ExtractionError(f"{self.source.source_id}: unclosed HTML tag(s): {self.tags}")
        if self.structure_errors:
            detail = self.structure_errors[0]
            raise ExtractionError(f"{self.source.source_id}: malformed HTML: {detail}")
        self._finalise_footnotes()
        references: set[tuple[str, str]] = set()
        for row in self.rows:
            document_id = str(row["document_id"])
            references.update((document_id, ref) for ref in row["footnote_refs"])
        footnotes = {
            (str(row["document_id"]), str(row["footnote_id"]))
            for row in self.rows
            if row["kind"] == "footnote" and isinstance(row["footnote_id"], str)
        }
        missing = references - footnotes
        if missing:
            document_id, identifier = sorted(missing)[0]
            raise ExtractionError(f"{document_id}: unresolved footnote reference: {identifier}")

    def _closes(self, roots: list[tuple[str, int]], tag: str, depth: int) -> bool:
        return bool(roots and roots[-1] == (tag, depth))

    def _closes_footnote(self, tag: str, depth: int) -> bool:
        return bool(
            self.footnote_roots
            and self.footnote_roots[-1].tag == tag
            and self.footnote_roots[-1].depth == depth
        )

    def _register_footnote_candidate(
        self, tag: str, classes: set[str], identifier: str | None
    ) -> None:
        if not identifier or not self.footnote_roots:
            return
        if tag == "a" or "target" in classes:
            self.footnote_roots[-1].candidate_ids.add(identifier)

    def _unwind_to(self, index: int) -> None:
        for depth in range(len(self.tags), index + 1, -1):
            self._close_semantic_tag(self.tags[depth - 1], depth)
        del self.tags[index + 1 :]

    def _close_semantic_tag(self, tag: str, depth: int) -> None:
        if not self.in_article:
            return
        if self._closes(self.skip_roots, tag, depth):
            self.skip_roots.pop()
        elif self.skip_roots:
            return
        elif self._closes(self.toc_roots, tag, depth):
            self.toc_roots.pop()
        elif self.toc_roots:
            return
        elif self._closes_footnote(tag, depth):
            self.flush()
            self.completed_footnotes.append(self.footnote_roots.pop())
        elif self.footnote_roots:
            if tag in _BLOCK_TAGS:
                self.flush()
        elif self.heading_depth == depth and self.heading_level is not None:
            self._finish_heading()
        elif tag in _BLOCK_TAGS or tag == "article":
            self.flush()

    def _finish_heading(self) -> None:
        heading = _normalise("".join(self.heading_parts))
        if heading and self.heading_level is not None:
            level = self.heading_level
            self.path = self.path[: level - 2]
            self.path.append(heading)
            self.started = True
        self.heading_level = None
        self.heading_depth = None
        self.heading_parts = []

    def _finalise_footnotes(self) -> None:
        references = {ref for row in self.rows for ref in row["footnote_refs"]}
        emitted: dict[str, _FootnoteContext] = {}
        for context in self.completed_footnotes:
            identifier = context.container_id or self._select_footnote_candidate(
                context, references
            )
            if identifier and context.rows:
                previous = emitted.get(identifier)
                if previous is not None and previous is not context:
                    raise ExtractionError(
                        f"{self.source.source_id}: duplicate footnote id: {identifier}"
                    )
                emitted[identifier] = context
            for row in context.rows:
                row["footnote_id"] = identifier

    def _select_footnote_candidate(
        self, context: _FootnoteContext, references: set[str]
    ) -> str | None:
        candidates = context.candidate_ids
        matching = candidates & references
        if len(matching) == 1:
            return next(iter(matching))
        if len(candidates) == 1:
            return next(iter(candidates))
        if len(candidates) > 1:
            raise ExtractionError(f"{self.source.source_id}: ambiguous footnote id")
        return None

    def _handle_break(self) -> None:
        if self.parts:
            self.br_count += 1
            if self.br_count >= 2:
                self.flush()
            else:
                self.parts.append(" ")


def extract_acquisition(input_root: Path, out: Path) -> dict[str, Any]:
    """Validate all acquired HTML before writing a complete private extraction output."""
    sources = _load_sources(input_root)
    verified: list[tuple[AcquiredSource, bytes]] = []
    for source in sources:
        path = _resolve(input_root, source.file)
        if not path.is_file():
            raise ExtractionError(f"{source.source_id}: missing HTML file: {source.file}")
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != source.sha256:
            raise ExtractionError(f"{source.source_id}: SHA-256 mismatch")
        verified.append((source, data))
    documents: list[dict[str, Any]] = []
    paragraphs: list[dict[str, Any]] = []
    per_source: list[dict[str, Any]] = []
    for source, data in verified:
        try:
            parser = _ArticleParser(source)
            parser.feed(data.decode("utf-8"))
            parser.close()
        except UnicodeDecodeError as exc:
            raise ExtractionError(f"{source.source_id}: HTML is not UTF-8") from exc
        parser.validate()
        if not parser.rows:
            raise ExtractionError(f"{source.source_id}: no article paragraphs extracted")
        paragraphs.extend(parser.rows)
        reasons = Counter(reason for row in parser.rows for reason in row["review_reasons"])
        documents.append(
            {
                "document_id": source.source_id,
                "title": source.title,
                "source_file": source.file,
                "source_sha256": source.sha256,
                "language": source.language,
                "parser_version": PARSER_VERSION,
            }
        )
        per_source.append(
            {
                "source_id": source.source_id,
                "paragraphs": len(parser.rows),
                "review_required": sum(
                    row["extraction_status"] == "review_required" for row in parser.rows
                ),
                "review_reasons": dict(sorted(reasons.items())),
            }
        )
    version = _digest({"parser_version": PARSER_VERSION, "sources": [s.__dict__ for s in sources]})
    report = {
        "schema_version": "1",
        "parser_version": PARSER_VERSION,
        "extraction_version": f"wiki-extract-{version[:16]}",
        "sources": per_source,
        "totals": {
            "sources": len(sources),
            "paragraphs": len(paragraphs),
            "review_required": sum(
                row["extraction_status"] == "review_required" for row in paragraphs
            ),
        },
    }
    _write_atomic(out, documents, paragraphs, report)
    return report


def _load_sources(root: Path) -> list[AcquiredSource]:
    records: list[dict[str, Any]] = []
    for name in ("acquisition.json", "profile-acquisition.json"):
        path = root / name
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExtractionError(f"cannot read {name}: {exc}") from exc
        sources = raw.get("sources") if isinstance(raw, dict) else None
        if not isinstance(sources, list):
            raise ExtractionError(f"{name}: 'sources' must be a list")
        records.extend(sources)
    result: list[AcquiredSource] = []
    seen: set[str] = set()
    for item in records:
        if not isinstance(item, dict):
            raise ExtractionError("acquisition source must be an object")
        fields = {
            key: item.get(key) for key in ("source_id", "title", "file", "language", "sha256")
        }
        if not all(isinstance(value, str) and value for value in fields.values()):
            raise ExtractionError("acquisition source has missing required string fields")
        source = AcquiredSource(**fields)  # type: ignore[arg-type]
        if source.source_id in seen:
            raise ExtractionError(f"duplicate source_id: {source.source_id}")
        if len(source.sha256) != 64 or set(source.sha256) - set("0123456789abcdef"):
            raise ExtractionError(f"{source.source_id}: invalid SHA-256")
        seen.add(source.source_id)
        result.append(source)
    if len(result) != 4:
        raise ExtractionError(f"expected exactly 4 acquired sources, found {len(result)}")
    return sorted(result, key=lambda source: source.source_id)


def _resolve(root: Path, relative: str) -> Path:
    base, candidate = root.resolve(), (root / relative).resolve()
    if not candidate.is_relative_to(base):
        raise ExtractionError(f"acquisition file escapes input root: {relative!r}")
    return candidate


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _digest(value: object) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _write_atomic(
    out: Path,
    documents: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    report: dict[str, Any],
) -> None:
    out = out.resolve()
    _assert_replaceable(out)
    staging = out.parent / f".{out.name}.tmp-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        write_jsonl(staging / "documents.jsonl", documents)
        write_jsonl(staging / "paragraphs.jsonl", paragraphs)
        write_json(staging / "extract_report.json", report)
        _swap(staging, out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _assert_replaceable(out: Path) -> None:
    if not out.exists():
        return
    if not out.is_dir():
        raise ExtractionError(f"--out is not a directory: {out}")
    expected = {"documents.jsonl", "paragraphs.jsonl", "extract_report.json"}
    if {path.name for path in out.iterdir()} != expected:
        raise ExtractionError(f"--out is not a complete extraction output: {out}")


def _swap(staging: Path, out: Path) -> None:
    previous = out.parent / f".{out.name}.old-{os.getpid()}"
    if out.exists():
        out.rename(previous)
    try:
        staging.rename(out)
    except BaseException:
        if previous.exists():
            previous.rename(out)
        raise
    if previous.exists():
        shutil.rmtree(previous)
