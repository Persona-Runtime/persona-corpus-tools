from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from .storage import ReviewError, load_annotations, load_prepared, save_annotation


class Update(BaseModel):
    segments: list[dict[str, Any]]


def create_app(
    prepared: Path, annotations: Path, media_root: Path | None = None, media_file: str | None = None
) -> FastAPI:
    metadata, rows = load_prepared(prepared)
    by_id = {str(row["subtitle_id"]): row for row in rows}
    app = FastAPI(title="Persona SMI reviewer")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        video = (
            f'<video controls src="/api/media/{media_file}"></video>'
            if media_file
            else "<p>Video not configured.</p>"
        )
        page = [
            "<!doctype html><title>SMI reviewer</title>",
            "<style>body{font:16px sans-serif;max-width:900px;margin:2rem auto}",
            "button{margin:.2rem}#cue{white-space:pre-wrap;padding:1rem;border:1px solid #ccc}</style>",  # noqa: E501
            video,
            "<p id=status></p><div id=cue></div><p><button onclick=\"label('gintoki')\">G gintoki</button>",  # noqa: E501
            "<button onclick=\"label('other')\">O other</button><button onclick=\"label('non_dialogue')\">N non-dialogue</button>",  # noqa: E501
            '<button onclick="label(\'undetermined\')">U undetermined</button></p><button onclick="move(-1)">left</button>',  # noqa: E501
            "<button onclick=\"move(1)\">right</button><script>let rows=[],i=0;async function init(){rows=await (await fetch('/api/subtitles')).json();show()}",  # noqa: E501
            "function show(){let r=rows[i];cue.textContent=r.visible_text;status.textContent=`${i+1}/${rows.length} ${r.subtitle_id} ${r.start_ms}-${r.end_ms??'?'}ms`;let v=document.querySelector('video');if(v&&r.start_ms!=null)v.currentTime=r.start_ms/1000}",  # noqa: E501
            "async function label(x){let r=rows[i];await fetch('/api/subtitles/'+r.subtitle_id,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify({segments:[{start_char:0,end_char:r.visible_text.length,label:x}]})});r.label=x;move(1)}",  # noqa: E501
            "function move(n){i=Math.max(0,Math.min(rows.length-1,i+n));show()}document.onkeydown=e=>{let m={g:'gintoki',o:'other',n:'non_dialogue',u:'undetermined'};if(m[e.key])label(m[e.key]);if(e.key==='ArrowLeft')move(-1);if(e.key==='ArrowRight')move(1)};init()</script>",  # noqa: E501
            "<p>Mixed cue: edit contiguous ranges as JSON, then save.</p><textarea id=segments rows=6 cols=70></textarea><button onclick=saveSegments()>save ranges</button>",  # noqa: E501
            "<script>async function saveSegments(){let r=rows[i];let s=JSON.parse(segments.value);await fetch('/api/subtitles/'+r.subtitle_id,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify({segments:s})});move(1)}</script>",  # noqa: E501
        ]
        return "".join(page)

    @app.get("/api/subtitles")
    def subtitles() -> list[dict[str, Any]]:
        annotations_doc = load_annotations(annotations, metadata)
        entries = annotations_doc["subtitles"]
        return [
            {
                **row,
                "label": (entries.get(row["subtitle_id"], {}).get("segments") or [{}])[0].get(
                    "label"
                ),
            }
            for row in rows
            if not row["is_clear"]
        ]

    @app.get("/api/subtitles/{subtitle_id}")
    def subtitle(subtitle_id: str) -> dict[str, Any]:
        if subtitle_id not in by_id:
            raise HTTPException(404, "unknown subtitle")
        return by_id[subtitle_id]

    @app.put("/api/subtitles/{subtitle_id}")
    def update(subtitle_id: str, update: Update) -> dict[str, Any]:
        row = by_id.get(subtitle_id)
        if row is None:
            raise HTTPException(404, "unknown subtitle")
        try:
            save_annotation(annotations, metadata, row, update.segments)
        except ReviewError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True}

    @app.get("/api/media/{relative_path:path}")
    def media(relative_path: str) -> FileResponse:
        if media_root is None:
            raise HTTPException(404, "media is not configured")
        root, candidate = media_root.resolve(), (media_root / relative_path).resolve()
        if (
            not candidate.is_relative_to(root)
            or candidate.suffix.lower() not in {".mp4", ".webm", ".mkv"}
            or not candidate.is_file()
        ):
            raise HTTPException(404, "media not found")
        return FileResponse(candidate)

    return app
