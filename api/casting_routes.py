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
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, Request, Response, UploadFile
from pydantic import BaseModel

from agents import casting
from api.events import bus
from api import workspace as ws
from core import character_coverage as cc
from core.coverage import connect

router = APIRouter()

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")

# Where footage lands. Locally this is a folder; deployed it is a bucket
# prefix. Either way each upload gets its own directory, so two people
# dropping "scene1.mp4" do not overwrite each other and a clip can always be
# traced back to the upload it came from.
FOOTAGE_ROOT = Path(os.environ.get(
    "FOOTAGE_ROOT",
    Path(__file__).resolve().parents[2] / "footage",
))
CLIPS_DIR = FOOTAGE_ROOT / "clips"
CLIPS_DIR.mkdir(parents=True, exist_ok=True)


def upload_dir(workspace: str, kind: str) -> Path:
    """A fresh directory for one upload."""
    stamp = uuid.uuid4().hex[:8]
    target = FOOTAGE_ROOT / "uploads" / workspace / f"{kind}_{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def clip_path(take_id: str) -> Path:
    """Find a clip wherever it was uploaded to.

    Older takes live directly in clips/; newer ones sit under the upload that
    brought them in. Checking the flat folder first keeps the seeded demo fast.
    """
    direct = CLIPS_DIR / f"{take_id}.mp4"
    if direct.exists():
        return direct
    for found in (FOOTAGE_ROOT / "uploads").rglob(f"{take_id}.mp4"):
        return found
    return direct

_local_client = None


def client():
    global _local_client
    if _local_client is None:
        _local_client = connect()
    return _local_client


# --- the scenes -------------------------------------------------------------

class World(BaseModel):
    period: str = ""
    setting: str = ""
    notes: str = ""


@router.get("/api/day")
def get_day(request: Request, response: Response):
    """When the day starts and ends.

    Taken from the shoot day on record rather than typed in — a production
    already knows its call time. Editable, because plans change.
    """
    ch = client()
    mine = ws.read_from(ch, ws.workspace_id(request, response))
    rows = ch.query(
        f"""
        SELECT call_time, sunset, shoot_day
        FROM {DB}.shoot_days WHERE production_id = %(p)s
        ORDER BY shoot_day DESC LIMIT 1
        """,
        parameters={"p": mine},
    ).result_rows

    if rows:
        call_time, sunset, day = rows[0]
    else:
        day = date.today()
        call_time = datetime.combine(day, datetime.min.time()) + timedelta(hours=7)
        sunset = call_time + timedelta(hours=12)

    return {
        "shoot_day": str(day),
        "call_time": call_time.isoformat(),
        "wrap_time": sunset.isoformat(),
        "hours": round((sunset - call_time).total_seconds() / 3600, 1),
    }


class DayPlan(BaseModel):
    call_time: str
    wrap_time: str


@router.put("/api/day")
def set_day(body: DayPlan, request: Request, response: Response):
    """Move the call or the planned wrap."""
    ch = client()
    mine = ws.workspace_id(request, response)
    ws.fork(ch, mine)

    call_time = datetime.fromisoformat(body.call_time)
    wrap_time = datetime.fromisoformat(body.wrap_time)
    day = call_time.date()

    ch.command(
        f"ALTER TABLE {DB}.shoot_days DELETE WHERE production_id = %(p)s",
        parameters={"p": mine}, settings={"mutations_sync": 1},
    )
    scenes = [r[0] for r in ch.query(
        f"SELECT scene_id FROM {DB}.scenes WHERE production_id = %(p)s",
        parameters={"p": mine},
    ).result_rows]

    ch.insert("shoot_days", [[
        mine, day, "main", "set", call_time, None,
        call_time - timedelta(minutes=45), wrap_time, scenes,
    ]], column_names=["production_id", "shoot_day", "unit", "location_id",
                      "call_time", "wrap_time", "sunrise", "sunset",
                      "planned_scenes"])
    return {"call_time": body.call_time, "wrap_time": body.wrap_time}


@router.get("/api/world")
def get_world(request: Request, response: Response):
    """What world this production is set in.

    Everything QC calls an anachronism is judged against this. A coffee cup is
    only wrong because the scene is medieval, so somebody has to say so.
    """
    from agents.qc import world_of

    ch = client()
    mine = ws.workspace_id(request, response)
    period, setting, notes = world_of(ch, ws.read_from(ch, mine))
    return {"period": period, "setting": setting, "notes": notes}


@router.put("/api/world")
def set_world(body: World, request: Request, response: Response):
    """Change the world. Re-running QC then judges against the new one."""
    ch = client()
    mine = ws.workspace_id(request, response)
    ws.fork(ch, mine)
    ch.insert("production_world", [[
        mine, body.period.strip()[:200], body.setting.strip()[:200],
        body.notes.strip()[:400], datetime.now(),
    ]], column_names=["production_id", "period", "setting", "notes",
                      "updated_at"])
    return {"ok": True, "period": body.period, "setting": body.setting}


@router.post("/api/scenes/{scene_id}/recheck")
async def recheck(scene_id: str, request: Request, response: Response):
    """Look at this scene's footage again against the world as it stands now."""
    import asyncio

    ch = client()
    mine = ws.workspace_id(request, response)
    ws.fork(ch, mine)
    scene_id = ws.scene_for(ch, mine, scene_id)

    run = bus.start(scene_id)
    run.publish("qc", "started", "Looking at the footage again")
    asyncio.create_task(asyncio.to_thread(_recheck_scene, scene_id, mine, run))
    return {"run_id": run.run_id}


def _recheck_scene(scene_id: str, production_id: str, run) -> None:
    from agents import qc
    from google import genai

    ch = connect()
    gclient = genai.Client()
    period, setting, notes = qc.world_of(ch, production_id)

    ch.command(
        f"ALTER TABLE {DB}.take_problems DELETE WHERE scene_id = %(s)s",
        parameters={"s": scene_id},
    )

    takes = ch.query(
        f"SELECT setup_id, take_id FROM {DB}.takes WHERE scene_id = %(s)s "
        f"ORDER BY setup_id, take_no",
        parameters={"s": scene_id},
    ).result_rows

    flagged = 0
    for setup_id, take_id in takes:
        path = clip_path(take_id)
        if not path.exists():
            continue
        run.publish("qc", "working", f"Checking {take_id}")
        try:
            verdict = qc.check_take(gclient, path, period, setting, notes)
        except Exception as exc:
            run.publish("qc", "error", f"{take_id}: {type(exc).__name__}")
            continue

        qc.store(ch, production_id, scene_id, setup_id, take_id, verdict)
        blocking = [p for p in verdict.get("problems", [])
                    if p["severity"] == "blocking"]
        if blocking:
            flagged += 1
            run.publish("qc", "tool_result",
                        f"{take_id}: {blocking[0]['what'][:70]}")

    # everything above judges takes one at a time; this is the only check that
    # needs them side by side
    run.publish("continuity", "working", "Checking the angles cut together")
    try:
        from agents import continuity
        verdict = continuity.compare_scene(gclient, ch, scene_id, CLIPS_DIR)
        if verdict:
            continuity.store(ch, production_id, scene_id, verdict)
            run.publish(
                "continuity", "tool_result",
                verdict["one_line"][:110] if not verdict["will_cut"]
                else "The angles cut together",
            )
    except Exception as exc:
        run.publish("continuity", "error", type(exc).__name__)

    run.publish("orchestrator", "done",
                f"{flagged} of {len(takes)} takes can't be used")
    run.finish()


@router.delete("/api/workspace")
def clear_workspace(request: Request, response: Response):
    """Throw everything away and start from an empty day.

    Only touches this visitor's copy. The seeded demo is left alone, so the
    next person still arrives at something.
    """
    ch = client()
    mine = ws.workspace_id(request, response)

    # mutations_sync=2 waits for the delete to actually finish on every
    # replica. Without it ClickHouse returns immediately, the page reloads
    # before the rows are gone, and everything appears to come back.
    wait = {"mutations_sync": 2}

    for table in ("scenes", "setups", "takes", "take_analysis",
                  "take_characters", "characters", "shoot_days", "crew_hours",
                  "take_problems"):
        ch.command(
            f"ALTER TABLE {DB}.{table} DELETE WHERE production_id = %(p)s",
            parameters={"p": mine}, settings=wait,
        )
    ch.command(
        f"ALTER TABLE {DB}.character_requirements DELETE "
        f"WHERE startsWith(scene_id, %(p)s)",
        parameters={"p": mine}, settings=wait,
    )

    # an empty production, so nothing falls back to the shared demo
    ch.insert("scenes", [[
        mine, f"{mine}_sc000", 0.0, 0, "EXT", "DAY", "dialogue",
        "nothing_yet", [], "",
    ]], column_names=["production_id", "scene_id", "script_page",
                      "page_eighths", "int_ext", "day_night", "scene_type",
                      "location_id", "characters", "synopsis"])
    return {"cleared": mine}


@router.get("/api/scenes")
def scenes(request: Request, response: Response):
    """Every scene in the day's work.

    A scene is one continuous piece of story in one place — the canal street,
    the workshop. It is the unit people actually talk about, and the unit
    footage gets shot into.
    """
    ch = client()
    mine = ws.read_from(ch, ws.workspace_id(request, response))
    rows = ch.query(
        f"""
        SELECT s.scene_id, s.location_id, s.int_ext, s.day_night, s.synopsis,
               uniqExact(t.take_id) AS takes,
               uniqExact(t.setup_id) AS positions
        FROM {DB}.scenes AS s
        LEFT JOIN {DB}.takes AS t ON t.scene_id = s.scene_id
        WHERE s.production_id = %(p)s AND s.location_id != 'nothing_yet'
        GROUP BY s.scene_id, s.location_id, s.int_ext, s.day_night, s.synopsis
        ORDER BY s.scene_id
        """,
        parameters={"p": mine},
    ).result_rows
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
def add_scene(body: NewScene, request: Request, response: Response):
    """Start a new scene — somewhere else, or another time of day."""
    ch = client()
    mine = ws.workspace_id(request, response)
    ws.fork(ch, mine)

    used = [
        int(r[0].split("sc")[-1])
        for r in ch.query(
            f"SELECT scene_id FROM {DB}.scenes WHERE production_id = %(p)s",
            parameters={"p": mine},
        ).result_rows
    ]
    n = (max(used) + 1) if used else 1
    scene_id = f"{mine}_sc{n:03d}"

    ch.insert("scenes", [[
        mine, scene_id, float(n), 12,
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
def cast(scene_id: str, request: Request, response: Response):
    ch = client()
    scene_id = ws.scene_for(ch, ws.workspace_id(request, response), scene_id)
    rows = ch.query(
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
def rename(character_id: str, body: Rename,
           request: Request, response: Response):
    """The one thing a person types: what this face is called."""
    ch = client()
    mine = ws.workspace_id(request, response)
    ws.fork(ch, mine)
    character_id = ws.character_for(ch, mine, character_id)

    ch.command(
        f"ALTER TABLE {DB}.characters UPDATE name = %(n)s "
        f"WHERE character_id = %(c)s AND production_id = %(p)s",
        parameters={"n": body.name.strip()[:60], "c": character_id, "p": mine},
        settings={"mutations_sync": 1},
    )
    return {"character_id": character_id, "name": body.name.strip()[:60]}


@router.post("/api/characters/{character_id}/is/{other_id}")
def merge(character_id: str, other_id: str,
          request: Request, response: Response):
    """Two faces, one person.

    Face matching splits people it should not — a back-of-head shot and a
    close-up genuinely do not look alike. Rather than pretend otherwise, the
    AD says so once and every take moves across.
    """
    if character_id == other_id:
        return {"ok": False, "reason": "same person"}

    ch = client()
    mine = ws.workspace_id(request, response)
    ws.fork(ch, mine)
    character_id = ws.character_for(ch, mine, character_id)
    other_id = ws.character_for(ch, mine, other_id)

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

@router.get("/api/scenes/{scene_id}/problems")
def problems(scene_id: str, request: Request, response: Response):
    """What QC found — worst first."""
    ch = client()
    scene_id = ws.scene_for(ch, ws.workspace_id(request, response), scene_id)
    rows = ch.query(
        f"""
        SELECT take_id, category, severity, what, where_in_frame, at_seconds
        FROM {DB}.take_problems WHERE scene_id = %(s)s
        ORDER BY multiIf(severity = 'blocking', 0,
                         severity = 'warning', 1, 2), take_id
        """,
        parameters={"s": scene_id},
    ).result_rows
    return [
        {"take_id": r[0], "category": r[1], "severity": r[2], "what": r[3],
         "where": r[4], "at_seconds": r[5]}
        for r in rows
    ]


@router.get("/api/scenes/{scene_id}/matrix")
def coverage_matrix(scene_id: str, request: Request, response: Response):
    ch = client()
    scene_id = ws.scene_for(ch, ws.workspace_id(request, response), scene_id)
    rows = cc.matrix(ch, scene_id)
    return {"scene_id": scene_id, "bands": cc.BANDS,
            "band_help": cc.BAND_HELP, "band_label": cc.BAND_LABEL,
            "characters": cc.as_json(rows), "summary": cc.summarise(rows)}


class BandSetting(BaseModel):
    required: bool
    recover_cost_usd: int | None = None


@router.put("/api/scenes/{scene_id}/need/{character_id}/{band}")
def set_need(scene_id: str, character_id: str, band: str, body: BandSetting,
             request: Request, response: Response):
    """Tick a shot on or off for one person. The call reruns from this."""
    ch = client()
    mine = ws.workspace_id(request, response)
    ws.fork(ch, mine)
    scene_id = ws.scene_for(ch, mine, scene_id)
    character_id = ws.character_for(ch, mine, character_id)

    cost = body.recover_cost_usd or cc.DEFAULT_COST.get(band, 20000)
    ch.insert(
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


def _ingest(paths: list[Path], scene_id: str, setup_hint: str, run,
            finish: bool = True, precomputed: dict | None = None) -> None:
    """Wrapper so a failure is reported rather than swallowed by the thread."""
    try:
        _ingest_inner(paths, scene_id, setup_hint, run, finish, precomputed)
    except Exception as exc:
        run.publish("orchestrator", "error",
                    f"{type(exc).__name__} — {exc}"[:300])
        if finish:
            run.finish()


def _ingest_inner(paths: list[Path], scene_id: str, setup_hint: str, run,
                  finish: bool = True, precomputed: dict | None = None) -> None:
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

    # QC cannot call anything an anachronism without knowing the world the
    # production is set in.
    from agents.qc import world_of
    period, setting, world_notes = world_of(ch, production_id)
    if not setting:
        here = ch.query(
            f"SELECT replaceAll(location_id, '_', ' ') FROM {DB}.scenes "
            f"WHERE scene_id = %(s)s LIMIT 1",
            parameters={"s": scene_id},
        ).result_rows
        setting = here[0][0] if here else ""

    for path in paths:
        take_id = path.stem
        duration = _probe_duration(path)

        analysis = (precomputed or {}).get(take_id)
        if analysis is None:
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

        run.publish("qc", "working", f"Checking {take_id} for problems")
        # QC is the slowest step and the most likely to be cut off mid-stream.
        # Losing it costs a check, not the upload.
        try:
            from agents import qc
            verdict = qc.check_take(gclient, path, period, setting, world_notes)
            qc.store(ch, production_id, scene_id, setup_id, take_id, verdict)
            blocking = [p for p in verdict.get("problems", [])
                        if p["severity"] == "blocking"]
            run.publish(
                "qc", "tool_result",
                (f"{take_id}: {blocking[0]['what'][:70]}" if blocking
                 else f"{take_id}: clean"),
                {"usable": verdict.get("usable", True),
                 "problems": len(verdict.get("problems", []))},
            )
        except Exception as exc:
            run.publish("qc", "error", f"{take_id}: {type(exc).__name__}")

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

    run.publish("continuity", "working", "Checking the angles cut together")
    try:
        from agents import continuity
        verdict = continuity.compare_scene(gclient, ch, scene_id, CLIPS_DIR)
        if verdict:
            continuity.store(ch, production_id, scene_id, verdict)
            run.publish(
                "continuity", "tool_result",
                verdict["one_line"][:110] if not verdict["will_cut"]
                else "The angles cut together",
            )
    except Exception as exc:
        run.publish("continuity", "error", type(exc).__name__)

    if finish:
        run.publish("orchestrator", "done", f"{len(paths)} clip(s) taken in",
                    {"scenes": [scene_id], "shots": len(paths)})
        run.finish()


def _split_into_shots(source: Path, run) -> list[Path]:
    """Cut a long file at every camera change.

    A whole film is not a take. Every cut in it is a different camera position,
    which is what the coverage question is actually about, so the file has to
    be broken at those cuts before anything else can make sense of it.
    """
    from data.split_takes import cut, detect_cuts, duration_of

    run.publish("vision", "working", f"Finding the cuts in {source.name}")
    boundaries = detect_cuts(source, 0.35, 0.0, 0.0)
    end = duration_of(source)
    marks = [0.0] + boundaries + [end]

    run.publish("vision", "tool_result",
                f"{max(0, len(marks) - 2)} cuts found in "
                f"{end / 60:.0f} minutes")

    roll = f"U{uuid.uuid4().hex[:3].upper()}"
    made: list[Path] = []
    for i in range(len(marks) - 1):
        a, b = marks[i], marks[i + 1]
        if b - a < 1.5:            # flash frames are not shots
            continue
        out = source.parent / f"{roll}_C{len(made) + 1:03d}.mp4"
        cut(source, out, a, b)
        made.append(out)

    run.publish("vision", "tool_result", f"{len(made)} shots cut")
    return made


def _place_by_location(ch, run, workspace: str, clips: list[Path],
                       analyses: dict) -> dict[str, str]:
    """Group shots into scenes by where they were filmed.

    Nobody tells us where a scene starts and ends. But shots from the same
    place belong together, and that is what a scene is — so the location the
    Vision Agent reports becomes the scene, and a place we have not seen before
    becomes a new one.
    """
    existing = {
        r[1].replace("_", " "): r[0] for r in ch.query(
            f"SELECT scene_id, location_id FROM {DB}.scenes "
            f"WHERE production_id = %(p)s",
            parameters={"p": workspace},
        ).result_rows
    }
    used = [int(sid.split("sc")[-1]) for sid in existing.values()] or [0]
    nxt = max(used) + 1

    placed: dict[str, str] = {}
    for clip in clips:
        analysis = analyses.get(clip.stem, {})
        place = (analysis.get("location_label") or "unsorted").strip().lower()

        if place not in existing:
            scene_id = f"{workspace}_sc{nxt:03d}"
            ch.insert("scenes", [[
                workspace, scene_id, float(nxt), 12, "EXT", "DAY", "dialogue",
                place.replace(" ", "_")[:60],
                [], analysis.get("scene_summary", "")[:180],
            ]], column_names=["production_id", "scene_id", "script_page",
                              "page_eighths", "int_ext", "day_night",
                              "scene_type", "location_id", "characters",
                              "synopsis"])
            existing[place] = scene_id
            nxt += 1
            run.publish("script", "tool_result", f"New scene: {place}")

        placed[clip.stem] = existing[place]

    return placed


@router.post("/api/film")
async def upload_film(request: Request, response: Response,
                      file: UploadFile = File(...)):
    """Drop a whole film in and let it sort itself.

    Split at the cuts, watch every shot, group them into scenes by location,
    find the faces, check for problems. Nobody types anything.
    """
    ch = client()
    mine = ws.workspace_id(request, response)
    ws.fork(ch, mine)

    here = upload_dir(mine, "film")
    name = Path(file.filename or f"film_{uuid.uuid4().hex[:6]}.mp4").name
    target = here / name
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    run = bus.start(mine)
    run.publish("orchestrator", "started", f"Taking in {name}")
    asyncio.create_task(asyncio.to_thread(_ingest_film, target, mine, run))
    return {"run_id": run.run_id, "film": name}


def _ingest_film(source: Path, workspace: str, run) -> None:
    """Wrapper so a failure is reported rather than swallowed by the thread."""
    try:
        _ingest_film_inner(source, workspace, run)
    except Exception as exc:
        run.publish("orchestrator", "error",
                    f"Could not take in {source.name}: {type(exc).__name__} — {exc}"[:300])
        run.finish()


def _ingest_film_inner(source: Path, workspace: str, run) -> None:
    from agents.vision import analyse_clip
    from google import genai

    ch = connect()
    gclient = genai.Client()

    clips = _split_into_shots(source, run)
    if not clips:
        run.publish("orchestrator", "error", "No shots found in that file")
        run.finish()
        return

    analyses: dict[str, dict] = {}
    for i, clip in enumerate(clips, start=1):
        run.publish("vision", "working",
                    f"Watching shot {i} of {len(clips)}")
        try:
            analyses[clip.stem] = analyse_clip(gclient, {
                "clip_name": clip.stem, "camera_roll": clip.stem.split("_")[0],
                "duration_s": _probe_duration(clip), "start_s": 0.0,
                "path": str(clip),
            })
        except Exception as exc:
            run.publish("vision", "error", f"{clip.stem}: {type(exc).__name__}")

    # Work out the world before anything is judged against it, using frames
    # from across the film rather than one shot.
    run.publish("qc", "working", "Working out what world this is set in")
    try:
        from agents import continuity, qc
        sample = [c for c in clips[:: max(1, len(clips) // 6)]][:6]
        frames = [f for f in (continuity.grab_frame(c, 1.0) for c in sample) if f]
        if frames:
            guess = qc.infer_world(gclient, frames)
            ch.insert("production_world", [[
                workspace, guess.get("period", "")[:200],
                guess.get("setting", "")[:200], guess.get("notes", "")[:400],
                datetime.now(),
            ]], column_names=["production_id", "period", "setting", "notes",
                              "updated_at"])
            run.publish("qc", "tool_result",
                        f"Set in {guess.get('period', 'unknown')}"
                        f" — {guess.get('setting', '')}"[:120])
    except Exception as exc:
        run.publish("qc", "error", f"Could not read the world: {type(exc).__name__}")

    run.publish("script", "working", "Sorting the shots into scenes")
    placed = _place_by_location(ch, run, workspace, clips, analyses)

    by_scene: dict[str, list[Path]] = {}
    for clip in clips:
        scene_id = placed.get(clip.stem)
        if scene_id:
            by_scene.setdefault(scene_id, []).append(clip)

    for scene_id, group in by_scene.items():
        _ingest(group, scene_id, "", run, finish=False,
                precomputed=analyses)

    made = sorted(by_scene)
    run.publish(
        "orchestrator", "done",
        f"{len(clips)} shots across {len(by_scene)} scenes",
        {"scenes": made, "shots": len(clips)},
    )
    run.finish()


@router.post("/api/scenes/{scene_id}/footage")
async def upload_footage(scene_id: str, request: Request, response: Response,
                         background: BackgroundTasks,
                         files: list[UploadFile] = File(...),
                         setup_id: str = Form("")):
    """Drop clips here. Everything after this is automatic."""
    ch = client()
    mine = ws.workspace_id(request, response)
    ws.fork(ch, mine)
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

    asyncio.create_task(asyncio.to_thread(_ingest, saved, scene_id, setup_id, run))
    return {"run_id": run.run_id, "accepted": [p.name for p in saved]}
