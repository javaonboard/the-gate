"""Cast, coverage per person, and taking footage in.

Drop a clip on the page and it goes: saved, watched by Gemini, faces found and
matched against the people already in the scene, written to ClickHouse, and the
call reruns. Nobody types anything unless they want to correct a name.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile
from pydantic import BaseModel

from agents import casting
from api.events import bus
from core import character_coverage as cc
from core.coverage import connect

router = APIRouter()

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")
CLIPS_DIR = Path(__file__).resolve().parents[2] / "footage" / "clips"
CLIPS_DIR.mkdir(parents=True, exist_ok=True)

_local_client = None


def client():
    global _local_client
    if _local_client is None:
        _local_client = connect()
    return _local_client


# --- the cast ---------------------------------------------------------------

class Rename(BaseModel):
    name: str


@router.get("/api/scenes/{scene_id}/cast")
def cast(scene_id: str):
    rows = client().query(
        f"""
        SELECT c.character_id, c.name, c.face_uri, c.appearances, c.description
        FROM {DB}.characters AS c
        WHERE c.character_id IN (
            SELECT character_id FROM {DB}.take_characters WHERE scene_id = %(s)s
        )
        ORDER BY c.appearances DESC
        """,
        parameters={"s": scene_id},
    ).result_rows
    return [
        {"character_id": r[0], "name": r[1], "face_uri": r[2],
         "appearances": r[3], "description": r[4]}
        for r in rows
    ]


@router.patch("/api/characters/{character_id}")
def rename(character_id: str, body: Rename):
    """The one thing a person types: what this face is called."""
    client().command(
        f"ALTER TABLE {DB}.characters UPDATE name = %(n)s "
        f"WHERE character_id = %(c)s",
        parameters={"n": body.name.strip()[:60], "c": character_id},
    )
    return {"character_id": character_id, "name": body.name.strip()[:60]}


# --- coverage, per person ---------------------------------------------------

@router.get("/api/scenes/{scene_id}/matrix")
def coverage_matrix(scene_id: str):
    rows = cc.matrix(client(), scene_id)
    return {"scene_id": scene_id, "bands": cc.BANDS,
            "band_help": cc.BAND_HELP, "band_label": cc.BAND_LABEL,
            "characters": cc.as_json(rows), "summary": cc.summarise(rows)}


class BandSetting(BaseModel):
    required: bool
    recover_cost_usd: int | None = None


@router.put("/api/scenes/{scene_id}/need/{character_id}/{band}")
def set_need(scene_id: str, character_id: str, band: str, body: BandSetting):
    """Tick a shot on or off for one person. The call reruns from this."""
    cost = body.recover_cost_usd or cc.DEFAULT_COST.get(band, 20000)
    client().insert(
        "character_requirements",
        [[scene_id, character_id, band, int(body.required), cost, datetime.now()]],
        column_names=["scene_id", "character_id", "shot_type", "required",
                      "recover_cost_usd", "updated_at"],
    )
    return {"ok": True, "character_id": character_id, "band": band,
            "required": body.required}


# --- taking footage in ------------------------------------------------------

def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _ingest(paths: list[Path], scene_id: str, setup_hint: str, run) -> None:
    """Watch each clip, find the faces, write it all down."""
    from agents.vision import analyse_clip
    from google import genai

    ch = connect()
    gclient = genai.Client()

    row = ch.query(
        f"SELECT production_id, location_id FROM {DB}.scenes WHERE scene_id = %(s)s",
        parameters={"s": scene_id},
    ).result_rows
    production_id = row[0][0] if row else "prod_now"
    shoot_day = date.today()

    for path in paths:
        take_id = path.stem
        duration = _probe_duration(path)

        run.publish("vision", "working", f"Watching {take_id}")
        analysis = analyse_clip(gclient, {
            "clip_name": take_id, "camera_roll": take_id.split("_")[0],
            "duration_s": duration, "start_s": 0.0, "path": str(path),
        })
        run.publish("vision", "tool_result",
                    f"{take_id}: {analysis['shot_size']}, "
                    f"{analysis['subjects_count']} in frame")

        setup_id = setup_hint or f"{scene_id}_{analysis['shot_size']}"
        take_no = ch.query(
            f"SELECT count() + 1 FROM {DB}.takes WHERE setup_id = %(u)s",
            parameters={"u": setup_id},
        ).result_rows[0][0]

        ch.insert("takes", [[
            production_id, shoot_day, scene_id, setup_id, take_no, take_id,
            take_id.split("_")[0], take_id, "10:00:00:00", "10:00:00:00",
            duration, 0.0, 0.0, "none", 800, 24.0, "SONY_F65", "complete", 0,
            0.0, f"file://../footage/clips/{path.name}",
        ]], column_names=[
            "production_id", "shoot_day", "scene_id", "setup_id", "take_no",
            "take_id", "camera_roll", "clip_name", "tc_start", "tc_end",
            "duration_s", "lens_mm", "t_stop", "nd", "iso", "fps", "camera_body",
            "status", "circled", "slate_confidence", "proxy_uri"])

        ch.insert("take_analysis", [[
            production_id, shoot_day, scene_id, setup_id, take_id,
            analysis["shot_size"], analysis["movement"],
            analysis.get("subjects", []), analysis["screen_direction"],
            analysis.get("eyeline_target", ""), float(analysis["focus_score"]),
            float(analysis["exposure_score"]),
            analysis.get("technical_faults", []),
            int(bool(analysis.get("vfx_clean_plate"))),
            int(bool(analysis.get("vfx_chart_visible"))), 0,
            int(bool(analysis.get("vfx_markers_visible"))), 0,
            analysis.get("model_id", ""), datetime.now(),
        ]], column_names=[
            "production_id", "shoot_day", "scene_id", "setup_id", "take_id",
            "shot_size", "movement", "subjects", "screen_direction",
            "eyeline_target", "focus_score", "exposure_score",
            "continuity_flags", "vfx_clean_plate", "vfx_chart", "vfx_grey_ball",
            "vfx_markers", "vfx_lens_grid", "model_id", "analysed_at"])

        run.publish("casting", "working", f"Looking for faces in {take_id}")
        known = ch.query(
            f"SELECT count() FROM {DB}.characters WHERE production_id = %(p)s",
            parameters={"p": production_id},
        ).result_rows[0][0]
        links = casting.analyse_take(gclient, ch, production_id, scene_id,
                                     setup_id, take_id, path, duration, known)
        fresh = sum(1 for l in links if l["matched_by"] == "new")
        run.publish("casting", "tool_result",
                    f"{len(links)} face(s), {fresh} new"
                    if links else "no faces found")

    run.publish("orchestrator", "done", f"{len(paths)} clip(s) taken in")
    run.finish()


@router.post("/api/scenes/{scene_id}/footage")
async def upload_footage(scene_id: str, background: BackgroundTasks,
                         files: list[UploadFile] = File(...),
                         setup_id: str = Form("")):
    """Drop clips here. Everything after this is automatic."""
    run = bus.start(scene_id)
    run.publish("orchestrator", "started",
                f"{len(files)} clip(s) off the card")

    saved: list[Path] = []
    for upload in files:
        name = Path(upload.filename or f"clip_{uuid.uuid4().hex[:6]}.mp4").name
        target = CLIPS_DIR / name
        with target.open("wb") as out:
            shutil.copyfileobj(upload.file, out)
        saved.append(target)

    asyncio.create_task(asyncio.to_thread(_ingest, saved, scene_id, setup_id, run))
    return {"run_id": run.run_id, "accepted": [p.name for p in saved]}
