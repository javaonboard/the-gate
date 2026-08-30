"""Footage in, and out again.

These routes do almost nothing on purpose: save the file, start the pipeline,
return a run id the browser can follow over the event stream. The work is in
agents/intake.py, a route should hand off, not carry five hundred lines of
pipeline.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from fastapi import (APIRouter, BackgroundTasks, File, Form, HTTPException,
                     Request, Response, UploadFile)
from fastapi.responses import FileResponse

from agents.intake import clip_path, ingest_clips, ingest_film, upload_dir
from api import workspace as ws
from api.events import bus
from api.shared import client

router = APIRouter()


@router.post("/api/film")
async def upload_film(request: Request, response: Response,
                      file: UploadFile = File(...)):
    """Drop a whole film in and let it sort itself.

    Split at the cuts, watch every shot, group them into scenes by location,
    find the faces, check for problems. Nobody types anything.
    """
    ch = client()
    mine = ws.writable(request, response)

    here = upload_dir(mine, "film")
    name = Path(file.filename or f"film_{uuid.uuid4().hex[:6]}.mp4").name
    target = here / name
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    run = bus.start(mine)
    run.publish("orchestrator", "started", f"Taking in {name}")
    asyncio.create_task(asyncio.to_thread(ingest_film, target, mine, run))
    return {"run_id": run.run_id, "film": name}


@router.post("/api/scenes/{scene_id}/footage")
async def upload_footage(scene_id: str, request: Request, response: Response,
                         background: BackgroundTasks,
                         files: list[UploadFile] = File(...),
                         setup_id: str = Form("")):
    """Drop clips here. Everything after this is automatic."""
    ch = client()
    mine = ws.writable(request, response)
    scene_id = ws.scene_for(ch, mine, scene_id)

    run = bus.start(scene_id)
    run.publish("orchestrator", "started",
                f"{len(files)} clip(s) off the card")

    here = upload_dir(mine, "clips")
    saved: list[Path] = []
    for upload in files:
        name = Path(upload.filename or f"clip_{uuid.uuid4().hex[:6]}.mp4").name
        target = here / name
        with target.open("wb") as out:
            shutil.copyfileobj(upload.file, out)
        saved.append(target)

    asyncio.create_task(asyncio.to_thread(ingest_clips, saved, scene_id, setup_id, run))
    return {"run_id": run.run_id, "accepted": [p.name for p in saved]}


@router.get("/api/takes/{take_id}/video")
def take_video(take_id: str):
    """The clip itself, so a take can be watched where it is listed.

    FileResponse answers range requests, which is what lets the player seek
    rather than downloading the whole take before it will play.
    """
    path = clip_path(take_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="No clip for that take")
    return FileResponse(path, media_type="video/mp4", filename=path.name)
