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


# --- the scenes -------------------------------------------------------------

@router.get("/api/scenes")
def scenes():
    """Every scene in the day's work.

    A scene is one continuous piece of story in one place — the canal street,
    the workshop. It is the unit people actually talk about, and the unit
    footage gets shot into.
    """
    rows = client().query(
        f"""
        SELECT s.scene_id, s.location_id, s.int_ext, s.day_night, s.synopsis,
               uniqExact(t.take_id) AS takes,
               uniqExact(t.setup_id) AS positions
        FROM {DB}.scenes AS s
        LEFT JOIN {DB}.takes AS t ON t.scene_id = s.scene_id
        WHERE s.production_id = 'prod_now'
        GROUP BY s.scene_id, s.location_id, s.int_ext, s.day_night, s.synopsis
        ORDER BY s.scene_id
        """
    ).result_rows
    ch = client()
    out = []
    for r in rows:
        # Each scene carries its own state, so the AD can see which ones are
        # short without opening them.
        summary = cc.summarise(cc.matrix(ch, r[0])) if r[5] else {
            "characters": 0, "required": 0, "have": 0,
            "completeness": 0.0, "missing": [], "exposure_usd": 0,
        }
        out.append({
            "scene_id": r[0],
            "number": r[0].split("sc")[-1].lstrip("0") or "0",
            "place": r[1].replace("_", " "),
            "where": "inside" if r[2] == "INT" else "outside",
            "when": r[3].lower(),
            "synopsis": r[4],
            "takes": r[5],
            "positions": r[6],
            "people": summary["characters"],
            "have": summary["have"],
            "required": summary["required"],
            "missing": len(summary["missing"]),
            "exposure_usd": summary["exposure_usd"],
            "complete": summary["required"] > 0 and not summary["missing"],
        })
    return out


class NewScene(BaseModel):
    place: str
    interior: bool = False
    when: str = "DAY"


@router.post("/api/scenes")
def add_scene(body: NewScene):
    """Start a new scene — somewhere else, or another time of day."""
    ch = client()
    used = [
        int(r[0].split("sc")[-1])
        for r in ch.query(
            f"SELECT scene_id FROM {DB}.scenes WHERE production_id = 'prod_now'"
        ).result_rows
    ]
    n = (max(used) + 1) if used else 1
    scene_id = f"prod_now_sc{n:03d}"

    ch.insert("scenes", [[
        "prod_now", scene_id, float(n), 12,
        "INT" if body.interior else "EXT", body.when.upper(), "dialogue",
        body.place.strip().replace(" ", "_")[:60] or f"location_{n}",
        [], "",
    ]], column_names=["production_id", "scene_id", "script_page",
                      "page_eighths", "int_ext", "day_night", "scene_type",
                      "location_id", "characters", "synopsis"])
    return {"scene_id": scene_id, "number": str(n),
            "place": body.place.strip()}


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


@router.post("/api/characters/{character_id}/is/{other_id}")
def merge(character_id: str, other_id: str):
    """Two faces, one person.

    Face matching splits people it should not — a back-of-head shot and a
    close-up genuinely do not look alike. Rather than pretend otherwise, the
    AD says so once and every take moves across.
    """
    if character_id == other_id:
        return {"ok": False, "reason": "same person"}

    ch = client()
    rows = ch.query(
        f"SELECT character_id, name, appearances FROM {DB}.characters FINAL "
        f"WHERE character_id IN (%(a)s, %(b)s)",
        parameters={"a": character_id, "b": other_id},
    ).result_rows
    if len(rows) != 2:
        return {"ok": False, "reason": "not found"}

    # keep whichever has been seen more; it has the better face crop
    by_id = {r[0]: r for r in rows}
    keep, drop = sorted(
        (by_id[character_id], by_id[other_id]), key=lambda r: -r[2]
    )

    # character_id is part of the sort key on both tables, so it cannot be
    # updated in place. Copy the rows across under the surviving id, then drop
    # the originals.
    moved = ch.query(
        f"""
        SELECT production_id, scene_id, setup_id, take_id, confidence, bbox,
               prominence, matched_by
        FROM {DB}.take_characters WHERE character_id = %(d)s
        """,
        parameters={"d": drop[0]},
    ).result_rows
    if moved:
        ch.insert(
            "take_characters",
            [[r[0], r[1], r[2], r[3], keep[0], r[4], list(r[5]), r[6], "merged"]
             for r in moved],
            column_names=["production_id", "scene_id", "setup_id", "take_id",
                          "character_id", "confidence", "bbox", "prominence",
                          "matched_by"],
        )
    ch.command(
        f"ALTER TABLE {DB}.take_characters DELETE WHERE character_id = %(d)s",
        parameters={"d": drop[0]},
    )

    needs = ch.query(
        f"""
        SELECT scene_id, shot_type, required, recover_cost_usd
        FROM {DB}.character_requirements FINAL WHERE character_id = %(d)s
        """,
        parameters={"d": drop[0]},
    ).result_rows
    if needs:
        ch.insert(
            "character_requirements",
            [[r[0], keep[0], r[1], r[2], r[3], datetime.now()] for r in needs],
            column_names=["scene_id", "character_id", "shot_type", "required",
                          "recover_cost_usd", "updated_at"],
        )
    ch.command(
        f"ALTER TABLE {DB}.character_requirements DELETE "
        f"WHERE character_id = %(d)s",
        parameters={"d": drop[0]},
    )
    ch.command(
        f"ALTER TABLE {DB}.characters UPDATE appearances = %(n)s "
        f"WHERE character_id = %(k)s",
        parameters={"n": keep[2] + drop[2], "k": keep[0]},
    )
    ch.command(
        f"ALTER TABLE {DB}.characters DELETE WHERE character_id = %(d)s",
        parameters={"d": drop[0]},
    )
    return {"ok": True, "kept": keep[0], "name": keep[1], "removed": drop[0]}


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


# --- the day's setups -------------------------------------------------------

class NewSetup(BaseModel):
    label: str = ""


@router.get("/api/scenes/{scene_id}/setups")
def setups(scene_id: str):
    """The camera positions planned for this scene, and what has landed in each."""
    rows = client().query(
        f"""
        SELECT s.setup_id, s.start_ts, s.actual_duration_s,
               uniqExact(t.take_id) AS takes,   -- both joins fan out; count once
               anyIf(a.shot_size, a.shot_size != '') AS shot_size
        FROM {DB}.setups AS s
        LEFT JOIN {DB}.takes AS t ON t.setup_id = s.setup_id
        LEFT JOIN {DB}.take_analysis AS a ON a.setup_id = s.setup_id
        WHERE s.scene_id = %(s)s
        GROUP BY s.setup_id, s.start_ts, s.actual_duration_s
        ORDER BY s.setup_id
        """,
        parameters={"s": scene_id},
    ).result_rows
    return [
        {"setup_id": r[0], "label": r[0].split("_")[-1],
         "takes": r[3], "shot_size": r[4] or "", "shot": bool(r[3])}
        for r in rows
    ]


@router.post("/api/scenes/{scene_id}/setups")
def add_setup(scene_id: str, body: NewSetup):
    """Plan another camera position. Footage gets dropped onto it."""
    ch = client()
    row = ch.query(
        f"SELECT production_id, location_id, int_ext, day_night, scene_type "
        f"FROM {DB}.scenes WHERE scene_id = %(s)s",
        parameters={"s": scene_id},
    ).result_rows
    production_id, location_id, int_ext, day_night, scene_type = (
        row[0] if row else ("prod_now", "canal_street", "EXT", "DAY", "dialogue")
    )

    used = {
        r[0].split("_")[-1]
        for r in ch.query(
            f"SELECT setup_id FROM {DB}.setups WHERE scene_id = %(s)s",
            parameters={"s": scene_id},
        ).result_rows
    }
    letter = next(
        (c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in used),
        uuid.uuid4().hex[:2].upper(),
    )
    setup_id = f"{scene_id}_{body.label.strip() or letter}"
    now = datetime.now()

    ch.insert(
        "setups",
        [[production_id, date.today(), scene_id, setup_id, now, None,
          2400, 0, location_id, int_ext, day_night, scene_type, 0,
          "dp_lind", 62]],
        column_names=["production_id", "shoot_day", "scene_id", "setup_id",
                      "start_ts", "end_ts", "planned_duration_s",
                      "actual_duration_s", "location_id", "int_ext",
                      "day_night", "scene_type", "extras_count", "dp_id",
                      "crew_size"],
    )
    return {"setup_id": setup_id, "label": setup_id.split("_")[-1]}


@router.delete("/api/scenes/{scene_id}/setups/{setup_id}")
def remove_setup(scene_id: str, setup_id: str):
    ch = client()
    for table in ("setups", "takes", "take_analysis", "take_characters"):
        ch.command(
            f"ALTER TABLE {DB}.{table} DELETE WHERE setup_id = %(u)s",
            parameters={"u": setup_id},
        )
    return {"deleted": setup_id}


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

        # Dropped onto a setup, it belongs there. Dropped loose, it gets a
        # setup of its own named after the framing, so nothing is silently
        # lumped in with an unrelated camera position.
        setup_id = setup_hint or f"{scene_id}_{analysis['shot_size']}"
        if not setup_hint:
            exists = ch.query(
                f"SELECT count() FROM {DB}.setups WHERE setup_id = %(u)s",
                parameters={"u": setup_id},
            ).result_rows[0][0]
            if not exists:
                ch.insert("setups", [[
                    production_id, shoot_day, scene_id, setup_id,
                    datetime.now(), None, 2400, 0,
                    row[0][1] if row else "canal_street", "EXT", "DAY",
                    "dialogue", 0, "dp_lind", 62,
                ]], column_names=[
                    "production_id", "shoot_day", "scene_id", "setup_id",
                    "start_ts", "end_ts", "planned_duration_s",
                    "actual_duration_s", "location_id", "int_ext", "day_night",
                    "scene_type", "extras_count", "dp_id", "crew_size"])
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
