"""Golden tests for the §4-1 "paste" export contract (markdown body, speech lines)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from persona_corpus_tools.reporting.markdown_export import render_speech_lines, render_wiki_markdown
from persona_corpus_tools.smi.cli import export_main
from persona_corpus_tools.smi.parser import parse_smi
from persona_corpus_tools.smi.reviewer import create_app
from persona_corpus_tools.smi.storage import write_prepared
from persona_corpus_tools.wiki_html.cli import main as wiki_main


def _hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def _source(source_id: str, filename: str, body: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "title": f"title {source_id}",
        "file": filename,
        "language": "ko",
        "sha256": _hash(body),
    }


def _acquisition_with_html(root: Path, html: str) -> None:
    records = []
    for index in range(4):
        filename = f"source-{index}.html"
        (root / filename).write_text(html, encoding="utf-8")
        records.append(_source(f"source-{index}", filename, html))
    (root / "acquisition.json").write_text(json.dumps({"sources": records[:3]}), encoding="utf-8")
    (root / "profile-acquisition.json").write_text(
        json.dumps({"sources": records[3:]}), encoding="utf-8"
    )


def test_render_wiki_markdown_preserves_heading_hierarchy_and_footnotes() -> None:
    documents = [{"document_id": "d1"}]
    paragraphs = [
        {
            "document_id": "d1",
            "kind": "body",
            "text": "첫 문단이다",
            "heading_path": ["개요"],
            "footnote_refs": ["fn-1"],
        },
        {
            "document_id": "d1",
            "kind": "body",
            "text": "세부 문단이다",
            "heading_path": ["개요", "세부", "더 깊은 절"],
            "footnote_refs": [],
        },
        {
            "document_id": "d1",
            "kind": "footnote",
            "text": "각주 본문이다",
            "heading_path": ["개요"],
            "footnote_refs": [],
            "footnote_id": "fn-1",
        },
    ]

    text = render_wiki_markdown(documents, paragraphs)

    assert text == (
        "# 개요\n\n"
        "첫 문단이다[^1]\n\n"
        "## 세부\n\n"
        "### 더 깊은 절\n\n"
        "세부 문단이다\n\n"
        "## 각주\n\n"
        "[^1]: 각주 본문이다\n"
    )


def test_render_wiki_markdown_deeper_than_three_levels_caps_at_h3() -> None:
    documents = [{"document_id": "d1"}]
    paragraphs = [
        {
            "document_id": "d1",
            "kind": "body",
            "text": "네 번째 깊이 문단",
            "heading_path": ["1", "2", "3", "4"],
            "footnote_refs": [],
        }
    ]

    text = render_wiki_markdown(documents, paragraphs)

    assert text == ("# 1\n\n## 2\n\n### 3\n\n### 4\n\n네 번째 깊이 문단\n")


def test_render_speech_lines_format() -> None:
    text = render_speech_lines([("합성 인물", "안녕"), ("합성 인물", "잘 가")])

    assert text == "합성 인물: 안녕\n합성 인물: 잘 가\n"


def test_wiki_extract_cli_paste_format_writes_markdown_file(tmp_path: Path) -> None:
    root, out = tmp_path / "raw", tmp_path / "paste.md"
    root.mkdir()
    _acquisition_with_html(
        root,
        "<html><article><h2>개요</h2>합성 인물 소개 문단이다<br><br></article></html>",
    )

    code = wiki_main(["--input-root", str(root), "--out", str(out), "--format", "paste"])

    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "# 개요" in text
    assert "합성 인물 소개 문단이다" in text
    # paste mode must not touch the private jsonl review-output contract
    assert not (root / "documents.jsonl").exists()


def _smi_rows() -> list[dict[str, object]]:
    return [
        row.to_dict()
        for row in parse_smi(
            "<SYNC Start=100><P Class=KRCC>합성 인물<br>안녕<SYNC Start=200><P>&nbsp;".encode(
                "cp949"
            )
        )
    ]


def test_smi_export_cli_paste_format_writes_speech_lines(tmp_path: Path) -> None:
    prepared = tmp_path / "prepared"
    write_prepared(
        prepared,
        {
            "schema_version": "1",
            "parser_version": "subtitle-smi-v1",
            "source_id": "synthetic-ep001",
            "source_sha256": "a" * 64,
            "manifest_digest": "m",
            "character_id": "character_a",
            "series": "Synthetic",
            "episode": "1",
            "language": "ko",
            "input_type": "subtitle_smi",
        },
        _smi_rows(),
    )
    annotations = tmp_path / "annotations.json"
    # 리뷰 서버를 그대로 통해 유효한 annotations.json을 만든다 — 스키마를 손으로
    # 맞추면 digest·완결성 검사가 바뀔 때마다 이 테스트가 조용히 깨진다.
    client = TestClient(create_app(prepared, annotations))
    (cue,) = client.get("/api/subtitles").json()
    client.put(
        f"/api/subtitles/{cue['subtitle_id']}",
        json={
            "segments": [
                {"start_char": 0, "end_char": len(cue["visible_text"]), "label": "gintoki"}
            ]
        },
    )
    out = tmp_path / "speech.txt"

    code = export_main(
        [
            "--prepared",
            str(prepared),
            "--annotations",
            str(annotations),
            "--personas",
            "configs/personas.yaml",
            "--out",
            str(out),
            "--format",
            "paste",
        ]
    )

    assert code == 0
    assert out.read_text(encoding="utf-8") == "gintoki: 합성 인물 안녕\n"
