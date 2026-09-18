from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from persona_corpus_tools.smi.export import export
from persona_corpus_tools.smi.parser import parse_smi
from persona_corpus_tools.smi.reviewer import create_app
from persona_corpus_tools.smi.storage import ReviewError, write_prepared


def _rows() -> list[dict[str, object]]:
    return [
        row.to_dict()
        for row in parse_smi(
            (
                "<SYNC Start=100><P Class=KRCC>긴토키<br>안녕"
                "<SYNC Start=200><P>&nbsp;<SYNC Start=150><P>역순"
            ).encode("cp949")
        )
    ]


def _prepared(path: Path) -> Path:
    metadata = {
        "schema_version": "1",
        "parser_version": "subtitle-smi-v1",
        "source_id": "gintama-k003",
        "source_sha256": "a" * 64,
        "manifest_digest": "m",
        "character_id": "character_a",
        "series": "Gintama",
        "episode": "3",
        "language": "ko",
        "input_type": "subtitle_smi",
    }
    write_prepared(path, metadata, _rows())
    return path


def test_cp949_sami_parser_preserves_clear_and_bad_timing() -> None:
    rows = _rows()
    assert rows[0]["subtitle_id"] == "s000001"
    assert rows[0]["visible_text"] == "긴토키\n안녕"
    assert rows[0]["korean_text"] == "긴토키\n안녕"
    assert rows[0]["end_ms"] == 200
    assert rows[1]["is_clear"] is True
    assert rows[2]["timing_issue"] == "missing_end"
    assert rows[2]["source_line"] == 1
    assert cast(int, rows[1]["source_byte"]) > cast(int, rows[0]["source_byte"])


def test_reviewer_writes_validated_annotation_and_confines_media(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path / "prepared")
    annotations = tmp_path / "private" / "annotations.json"
    media = tmp_path / "media"
    media.mkdir()
    (media / "clip.mp4").write_bytes(b"not a movie")
    client = TestClient(create_app(prepared, annotations, media, "clip.mp4"))
    rows = client.get("/api/subtitles").json()
    assert [row["subtitle_id"] for row in rows] == ["s000001", "s000003"]
    cue = rows[0]
    response = client.put(
        f"/api/subtitles/{cue['subtitle_id']}",
        json={
            "segments": [
                {"start_char": 0, "end_char": len(cue["visible_text"]), "label": "gintoki"}
            ]
        },
    )
    assert response.status_code == 200
    assert client.get("/api/media/../prepared/source.json").status_code == 404
    assert json.loads(annotations.read_text())["source_sha256"] == "a" * 64


def test_export_requires_complete_annotations_and_exports_only_confirmed_target(
    tmp_path: Path,
) -> None:
    prepared = _prepared(tmp_path / "prepared")
    annotations = tmp_path / "annotations.json"
    app = TestClient(create_app(prepared, annotations))
    rows = app.get("/api/subtitles").json()
    first, second = rows
    app.put(
        f"/api/subtitles/{first['subtitle_id']}",
        json={
            "segments": [
                {"start_char": 0, "end_char": len(first["visible_text"]), "label": "gintoki"}
            ]
        },
    )
    with pytest.raises(ReviewError, match="missing"):
        export(prepared, annotations, Path("configs/personas.yaml"), tmp_path / "corpus")
    app.put(
        f"/api/subtitles/{second['subtitle_id']}",
        json={
            "segments": [
                {"start_char": 0, "end_char": len(second["visible_text"]), "label": "undetermined"}
            ]
        },
    )
    export(prepared, annotations, Path("configs/personas.yaml"), tmp_path / "corpus")
    parsed = [
        json.loads(line)
        for line in (tmp_path / "corpus" / "parsed" / "gintama-k003.jsonl").read_text().splitlines()
    ]
    assert len(parsed) == 1
    assert parsed[0]["source_locator"] == {
        "subtitle_id": "s000001",
        "start_ms": 100,
        "end_ms": 200,
        "source_line": 1,
        "start_char": 0,
        "end_char": 6,
    }
