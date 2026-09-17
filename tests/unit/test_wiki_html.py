from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from persona_ingestion.wiki_html.extractor import ExtractionError, extract_acquisition


def _source(source_id: str, filename: str, body: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "title": f"title {source_id}",
        "file": filename,
        "language": "ko",
        "sha256": hashlib.sha256(body.encode()).hexdigest(),
    }


def _acquisition(root: Path, bad_hash: bool = False) -> None:
    html = """<html><article><pre><code>template residue</code></pre><div class=toc>menu</div>
    <h2><a>1.</a> 개요</h2>첫 본문<a class=wikiFootnote href=#fn-1>[1]</a><br><br>둘째 &amp; 본문
    <h3>세부</h3><noscript><img src=external.png></noscript>
    <ul><li>목록 하나</li><li>목록 둘</li></ul>
    <span class=footnote-list id=fn-1>각주 본문</span></article><script>bad()</script></html>"""
    records: list[dict[str, str]] = []
    for index in range(4):
        filename = f"source-{index}.html"
        (root / filename).write_text(html, encoding="utf-8")
        source = _source(f"source-{index}", filename, html)
        if bad_hash and index == 0:
            source["sha256"] = "0" * 64
        records.append(source)
    (root / "acquisition.json").write_text(json.dumps({"sources": records[:3]}), encoding="utf-8")
    (root / "profile-acquisition.json").write_text(
        json.dumps({"sources": records[3:]}), encoding="utf-8"
    )


def _acquisition_with_html(root: Path, html: str) -> None:
    records: list[dict[str, str]] = []
    for index in range(4):
        filename = f"source-{index}.html"
        (root / filename).write_text(html, encoding="utf-8")
        records.append(_source(f"source-{index}", filename, html))
    (root / "acquisition.json").write_text(json.dumps({"sources": records[:3]}), encoding="utf-8")
    (root / "profile-acquisition.json").write_text(
        json.dumps({"sources": records[3:]}), encoding="utf-8"
    )


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_extracts_article_content_with_stable_provenance(tmp_path: Path) -> None:
    root, out = tmp_path / "raw", tmp_path / "out"
    root.mkdir()
    _acquisition(root)
    first = extract_acquisition(root, out)
    paragraphs = _jsonl(out / "paragraphs.jsonl")
    assert first["totals"]["sources"] == 4
    assert [row["text"] for row in paragraphs[:4]] == [
        "첫 본문",
        "둘째 & 본문",
        "목록 하나",
        "목록 둘",
    ]
    assert paragraphs[0]["heading_path"] == ["1. 개요"]
    assert paragraphs[0]["footnote_refs"] == ["fn-1"]
    assert paragraphs[2]["heading_path"] == ["1. 개요", "세부"]
    assert paragraphs[4]["kind"] == "footnote"
    assert paragraphs[4]["footnote_id"] == "fn-1"
    assert all("template" not in str(row["text"]) for row in paragraphs)
    before = (out / "paragraphs.jsonl").read_bytes()
    extract_acquisition(root, out)
    assert (out / "paragraphs.jsonl").read_bytes() == before


def test_rejects_bad_hash_without_replacing_prior_output(tmp_path: Path) -> None:
    root, out = tmp_path / "raw", tmp_path / "out"
    root.mkdir()
    _acquisition(root)
    extract_acquisition(root, out)
    before = (out / "extract_report.json").read_bytes()
    _acquisition(root, bad_hash=True)
    with pytest.raises(ExtractionError, match="SHA-256 mismatch"):
        extract_acquisition(root, out)
    assert (out / "extract_report.json").read_bytes() == before


def test_toc_void_element_does_not_hide_following_article_content(tmp_path: Path) -> None:
    root, out = tmp_path / "raw", tmp_path / "out"
    root.mkdir()
    _acquisition_with_html(
        root,
        """<html><article><div class=toc>menu<br>still menu</div>
        <h2>개요</h2><p>남은 본문</p><span class=footnote-list id=fn-1>각주</span>
        </article></html>""",
    )

    extract_acquisition(root, out)

    rows = _jsonl(out / "paragraphs.jsonl")
    assert [row["text"] for row in rows[:2]] == ["남은 본문", "각주"]
    assert [row["kind"] for row in rows[:2]] == ["body", "footnote"]


def test_footnote_void_element_does_not_absorb_following_section(tmp_path: Path) -> None:
    root, out = tmp_path / "raw", tmp_path / "out"
    root.mkdir()
    _acquisition_with_html(
        root,
        """<html><article><h2>개요</h2><p>본문</p>
        <span class=footnote-list id=fn-1>각주<br>계속</span>
        <h3>다음</h3><p>다음 본문</p></article></html>""",
    )

    extract_acquisition(root, out)

    rows = _jsonl(out / "paragraphs.jsonl")
    assert [row["text"] for row in rows[:3]] == ["본문", "각주 계속", "다음 본문"]
    assert rows[1]["footnote_id"] == "fn-1"
    assert rows[2]["kind"] == "body"
    assert rows[2]["heading_path"] == ["개요", "다음"]


def test_internal_footnote_anchor_links_body_reference(tmp_path: Path) -> None:
    root, out = tmp_path / "raw", tmp_path / "out"
    root.mkdir()
    _acquisition_with_html(
        root,
        """<html><article><h2>개요</h2><p>본문<a class=wikiFootnote href=#fn-1>[1]</a></p>
        <span class=footnote-list><a id=fn-1></a>각주</span></article></html>""",
    )

    extract_acquisition(root, out)

    rows = _jsonl(out / "paragraphs.jsonl")
    assert rows[0]["footnote_refs"] == ["fn-1"]
    assert rows[1]["footnote_id"] == "fn-1"


def test_container_footnote_id_is_not_overwritten_by_nested_element(tmp_path: Path) -> None:
    root, out = tmp_path / "raw", tmp_path / "out"
    root.mkdir()
    _acquisition_with_html(
        root,
        """<html><article><h2>개요</h2><p>본문<a class=wikiFootnote href=#fn-1>[1]</a></p>
        <span class=footnote-list id=fn-1><span class=target id=unrelated>각주</span></span>
        </article></html>""",
    )

    extract_acquisition(root, out)

    rows = _jsonl(out / "paragraphs.jsonl")
    assert rows[1]["footnote_id"] == "fn-1"


def test_rejects_reference_to_empty_footnote_without_replacing_prior_output(tmp_path: Path) -> None:
    root, out = tmp_path / "raw", tmp_path / "out"
    root.mkdir()
    _acquisition(root)
    extract_acquisition(root, out)
    before = (out / "extract_report.json").read_bytes()
    _acquisition_with_html(
        root,
        """<html><article><h2>개요</h2><p>본문<a class=wikiFootnote href=#fn-1>[1]</a></p>
        <span class=footnote-list id=fn-1></span></article></html>""",
    )

    with pytest.raises(ExtractionError, match="unresolved footnote reference"):
        extract_acquisition(root, out)

    assert (out / "extract_report.json").read_bytes() == before


def test_rejects_unresolved_footnote_without_replacing_prior_output(tmp_path: Path) -> None:
    root, out = tmp_path / "raw", tmp_path / "out"
    root.mkdir()
    _acquisition(root)
    extract_acquisition(root, out)
    before = (out / "extract_report.json").read_bytes()
    _acquisition_with_html(
        root,
        """<html><article><h2>개요</h2><p>본문<a class=wikiFootnote href=#fn-missing>[1]</a></p>
        <span class=footnote-list id=fn-1>각주</span></article></html>""",
    )

    with pytest.raises(ExtractionError, match="unresolved footnote reference"):
        extract_acquisition(root, out)

    assert (out / "extract_report.json").read_bytes() == before


def test_implicit_nav_close_does_not_hide_following_body(tmp_path: Path) -> None:
    root, out = tmp_path / "raw", tmp_path / "out"
    root.mkdir()
    _acquisition_with_html(
        root,
        """<html><article><div><nav>menu</div><h2>개요</h2><p>남은 본문</p>
        </article></html>""",
    )

    extract_acquisition(root, out)

    assert _jsonl(out / "paragraphs.jsonl")[0]["text"] == "남은 본문"


def test_nested_implicit_nav_close_does_not_hide_following_body(tmp_path: Path) -> None:
    root, out = tmp_path / "raw", tmp_path / "out"
    root.mkdir()
    _acquisition_with_html(
        root,
        """<html><article><h2>개요</h2><p>앞 본문</p><div><nav><span>목차</div>
        <p>뒤 본문</p></article></html>""",
    )

    extract_acquisition(root, out)

    rows = _jsonl(out / "paragraphs.jsonl")
    assert [row["text"] for row in rows[:2]] == ["앞 본문", "뒤 본문"]


def test_implicit_heading_close_at_body_block_extracts_body(tmp_path: Path) -> None:
    root, out = tmp_path / "raw", tmp_path / "out"
    root.mkdir()
    _acquisition_with_html(
        root,
        "<html><article><h2>개요<p>남은 본문</p></article></html>",
    )

    extract_acquisition(root, out)

    rows = _jsonl(out / "paragraphs.jsonl")
    assert rows[0]["heading_path"] == ["개요"]
    assert rows[0]["text"] == "남은 본문"


def test_rejects_truncated_html_without_replacing_prior_output(tmp_path: Path) -> None:
    root, out = tmp_path / "raw", tmp_path / "out"
    root.mkdir()
    _acquisition(root)
    extract_acquisition(root, out)
    before = (out / "extract_report.json").read_bytes()
    _acquisition_with_html(root, "<html><article><h2>개요</h2><p>잘린 본문")

    with pytest.raises(ExtractionError, match="unclosed <article>"):
        extract_acquisition(root, out)

    assert (out / "extract_report.json").read_bytes() == before


def test_hash_and_parsing_use_the_same_single_read_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, out = tmp_path / "raw", tmp_path / "out"
    root.mkdir()
    original = "<html><article><h2>개요</h2><p>검증한 본문</p></article></html>"
    replacement = "<html><article><h2>개요</h2><p>바뀐 본문</p></article></html>"
    _acquisition_with_html(root, original)
    read_bytes = Path.read_bytes
    reads = 0

    def read_snapshot(path: Path) -> bytes:
        nonlocal reads
        data = read_bytes(path)
        if path.name == "source-0.html":
            reads += 1
            if reads == 1:
                path.write_text(replacement, encoding="utf-8")
        return data

    monkeypatch.setattr(Path, "read_bytes", read_snapshot)
    extract_acquisition(root, out)

    assert reads == 1
    assert _jsonl(out / "paragraphs.jsonl")[0]["text"] == "검증한 본문"


def test_rejects_escaped_path_after_loading_all_four_sources(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    root.mkdir()
    escaped = {
        "source_id": "bad",
        "title": "bad",
        "file": "../outside.html",
        "language": "ko",
        "sha256": "a" * 64,
    }
    html = "<html><article><h2>개요</h2><p>본문</p></article></html>"
    valid = [_source(f"source-{index}", f"source-{index}.html", html) for index in range(3)]
    for source in valid:
        (root / source["file"]).write_text(html, encoding="utf-8")
    (root / "acquisition.json").write_text(
        json.dumps({"sources": [escaped, *valid]}), encoding="utf-8"
    )
    (root / "profile-acquisition.json").write_text(json.dumps({"sources": []}), encoding="utf-8")
    with pytest.raises(ExtractionError, match="escapes input root"):
        extract_acquisition(root, tmp_path / "out")
