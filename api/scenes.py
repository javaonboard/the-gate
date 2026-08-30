"""Scenes, and where the day stands across them.

A scene is one continuous piece of story in one place. The system infers them
from what the footage looks like, which is a guess a script supervisor can
correct, hence merging two that are really one room, adding one by hand, and
saying what a scene was for when nobody is in it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from agents.intake import clip_path, recheck_scene
from api import workspace as ws
from api.events import bus
from api.labels import MOVEMENTS, SHOT_SIZES
from api.shared import DB, DONE_BEFORE_REPLYING, client
from core import character_coverage as cc

router = APIRouter()


@router.post("/api/scenes/{scene_id}/recheck")
async def recheck(scene_id: str, request: Request, response: Response):
    """Look at this scene's footage again against the world as it stands now."""
    import asyncio

    ch = client()
    mine = ws.writable(request, response)
    scene_id = ws.scene_for(ch, mine, scene_id)

    run = bus.start(scene_id)
    run.publish("qc", "started", "Looking at the footage again")
    asyncio.create_task(asyncio.to_thread(recheck_scene, scene_id, mine, run))
    return {"run_id": run.run_id}


@router.get("/api/scenes")
def scenes(request: Request, response: Response):
    """Every scene in the day's work.

    A scene is one continuous piece of story in one place, the canal street,
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
    # Numbered by where they sit in the day, not by the id they were given.
    # Merging two scenes leaves a gap in the ids, and "scene 1, 3, 4, 5" reads
    # like a scene went missing rather than like two became one.
    for position, r in enumerate(rows, start=1):
        # Each scene carries its own state, so the AD can see which ones are
        # short without opening them.
        summary = cc.summarise(cc.matrix(ch, r[0]),
                               cc.scene_shots(ch, r[0])) if r[5] else {
            "characters": 0, "required": 0, "have": 0,
            "completeness": 0.0, "missing": [], "exposure_usd": 0,
        }
        out.append({
            "scene_id": r[0],
            "number": str(position),
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
    """Start a new scene, somewhere else, or another time of day."""
    ch = client()
    mine = ws.writable(request, response)

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


@router.get("/api/scenes/{scene_id}/problems")
def problems(scene_id: str, request: Request, response: Response):
    """What QC found, worst first."""
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
    scene = cc.scene_shots(ch, scene_id)
    takes = ch.query(
        f"SELECT count() FROM {DB}.takes WHERE scene_id = %(s)s",
        parameters={"s": scene_id},
    ).result_rows[0][0]
    return {"scene_id": scene_id, "bands": cc.BANDS,
            "band_help": cc.BAND_HELP, "band_label": cc.BAND_LABEL,
            "takes": takes,
            "characters": cc.as_json(rows),
            "scene_shots": cc.scene_as_json(scene),
            "summary": cc.summarise(rows, scene)}


class BandSetting(BaseModel):
    required: bool
    recover_cost_usd: int | None = None


@router.post("/api/scenes/{scene_id}/is/{other_id}")
def merge_scenes(scene_id: str, other_id: str,
                 request: Request, response: Response):
    """Two scenes, one place.

Grouping shots into scenes is inferred from what the footage looks like,
    and a room shot from one side and then the other genuinely looks like two
    rooms, that is the whole reason the 180-degree rule exists.
    """
    if scene_id == other_id:
        return {"ok": False, "reason": "same scene"}

    ch = client()
    mine = ws.writable(request, response)
    scene_id = ws.scene_for(ch, mine, scene_id)
    other_id = ws.scene_for(ch, mine, other_id)

    found = ch.query(
        f"SELECT scene_id, location_id FROM {DB}.scenes "
        f"WHERE production_id = %(p)s AND scene_id IN (%(a)s, %(b)s)",
        parameters={"p": mine, "a": scene_id, "b": other_id},
    ).result_rows
    if len(found) != 2:
        raise HTTPException(status_code=404, detail="No such scene")

    # The earlier scene wins: it is where the action started, and keeping the
    # lower number matches how a lined script reads.
    keep, drop = sorted(r[0] for r in found)

    for table in ("takes", "take_analysis", "take_characters", "take_problems",
                  "setups"):
        moved = ch.query(
            f"SELECT * FROM {DB}.{table} WHERE scene_id = %(d)s",
            parameters={"d": drop},
        )
        if not moved.result_rows:
            continue
        names = moved.column_names
        at = names.index("scene_id")
        ch.insert(
            table,
            [[keep if i == at else v for i, v in enumerate(row)]
             for row in moved.result_rows],
            column_names=list(names),
        )
        ch.command(
            f"ALTER TABLE {DB}.{table} DELETE WHERE scene_id = %(d)s",
            parameters={"d": drop}, settings=DONE_BEFORE_REPLYING,
        )

    # anything already said about what the emptied scene needed
    needs = ch.query(
        f"SELECT character_id, shot_type, required, recover_cost_usd "
        f"FROM {DB}.character_requirements FINAL WHERE scene_id = %(d)s",
        parameters={"d": drop},
    ).result_rows
    if needs:
        ch.insert(
            "character_requirements",
            [[keep, r[0], r[1], r[2], r[3], datetime.now()] for r in needs],
            column_names=["scene_id", "character_id", "shot_type", "required",
                          "recover_cost_usd", "updated_at"],
        )
    ch.command(
        f"ALTER TABLE {DB}.character_requirements DELETE WHERE scene_id = %(d)s",
        parameters={"d": drop}, settings=DONE_BEFORE_REPLYING,
    )
    ch.command(
        f"ALTER TABLE {DB}.scenes DELETE WHERE scene_id = %(d)s",
        parameters={"d": drop}, settings=DONE_BEFORE_REPLYING,
    )

    return {"ok": True, "kept": keep, "removed": drop}


@router.get("/api/today")
def today(request: Request, response: Response):
    """Where the whole day stands, not just the scene that happens to be open.

    The gate is asked scene by scene, because that is when a company move
    happens. But an AD also has to know where the day is as a whole, a scene
    finished and one barely started are the same "67%" on their own and very
    different facts about the day.
    """
    ch = client()
    mine = ws.read_from(ch, ws.workspace_id(request, response))

    scenes = ch.query(
        f"""
        SELECT s.scene_id, replaceAll(s.location_id, '_', ' '), s.int_ext,
               uniqExact(t.take_id) AS takes,
               uniqExact(t.setup_id) AS positions
        FROM {DB}.scenes AS s
        LEFT JOIN {DB}.takes AS t ON t.scene_id = s.scene_id
        WHERE s.production_id = %(p)s AND s.location_id != 'nothing_yet'
        GROUP BY s.scene_id, s.location_id, s.int_ext
        ORDER BY s.scene_id
        """,
        parameters={"p": mine},
    ).result_rows

    out, required, have, exposure, unjudged = [], 0, 0, 0, 0
    for position, (scene_id, place, int_ext, takes, positions) in enumerate(
            scenes, start=1):
        rows = cc.matrix(ch, scene_id)
        shots = cc.scene_shots(ch, scene_id)
        summary = cc.summarise(rows, shots)

        required += summary["required"]
        have += summary["have"]
        exposure += summary["exposure_usd"]
        if not summary["judged"]:
            unjudged += 1

        out.append({
            "scene_id": scene_id,
            # numbered by where it sits in the day, so merging two does not
            "number": str(position),
            "place": place,
            "where": "inside" if int_ext == "INT" else "outside",
            "takes": takes,
            "positions": positions,
            "judged": summary["judged"],
            "completeness": summary["completeness"],
            "required": summary["required"],
            "have": summary["have"],
            "exposure_usd": summary["exposure_usd"],
            "people": summary["characters"],
        })

    # One call for the day. A scene nobody has said anything about is not a
    # pass, it is the reason the day cannot be called yet, which is a
    # different thing from being short and worth saying differently.
    if required == 0:
        verdict = "NOT CHECKED"
    elif have < required or unjudged:
        verdict = "NO-GO"
    else:
        verdict = "GO"

    return {
        "scenes": out,
        "day": {
            "verdict": verdict,
            "scenes": len(out),
            "unjudged": unjudged,
            "required": required,
            "have": have,
            # Counted in shots, not averaged across scenes: a scene with one
            # shot outstanding and a scene with twelve do not weigh the same.
            "completeness": round(have / required, 3) if required else 0.0,
            "judged": required > 0,
            "exposure_usd": exposure,
        },
    }


@router.put("/api/scenes/{scene_id}/needs/{shot}")
def set_scene_need(scene_id: str, shot: str, body: BandSetting,
                   request: Request, response: Response):
    """Say this scene needs a shot of its own, an insert, a plate, a wide.

    The generated defaults come from who is on camera, which says nothing about
    a scene that is a hand on a door handle. This is where the AD says what it
    actually needs.
    """
    if shot not in cc.SCENE_SHOTS:
        raise HTTPException(status_code=400,
                            detail=f"Not a shot this scene can need: {shot}")

    ch = client()
    mine = ws.writable(request, response)
    scene_id = ws.scene_for(ch, mine, scene_id)

    cost = body.recover_cost_usd or cc.SCENE_COST.get(shot, 20000)
    ch.insert(
        "character_requirements",
        [[scene_id, cc.SCENE_ROW, shot, int(body.required), cost,
          datetime.now()]],
        column_names=["scene_id", "character_id", "shot_type", "required",
                      "recover_cost_usd", "updated_at"],
    )
    return {"ok": True, "shot": shot, "required": body.required}


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


@router.get("/api/scenes/{scene_id}/breakdown")
def breakdown(scene_id: str, request: Request, response: Response):
    """The scene, its footage, who is in each piece, and what is wrong.

    One call rather than four plus one per take: the point of the breakdown is
    that the levels are connected, and assembling it here means the connections
    are made once, from the same data the gate decides on.
    """
    ch = client()
    scene_id = ws.scene_for(ch, ws.workspace_id(request, response), scene_id)

    scene = ch.query(
        f"""
        SELECT scene_id, location_id, int_ext, day_night, synopsis
        FROM {DB}.scenes WHERE scene_id = %(s)s
        """,
        parameters={"s": scene_id},
    ).result_rows
    if not scene:
        raise HTTPException(status_code=404, detail="No such scene")
    s = scene[0]

    rows = cc.matrix(ch, scene_id)
    shots = cc.scene_shots(ch, scene_id)
    summary = cc.summarise(rows, shots)

    # which take gives which character which band, the same judgment the
    gives: dict[str, list[dict]] = {}
    for row in rows:
        for band, cell in row.cells.items():
            for take_id in cell.takes:
                gives.setdefault(take_id, []).append(
                    {"character_id": row.character_id, "band": band,
                     "band_label": cc.BAND_LABEL.get(band, band)})

    people = {
        r[0]: {"character_id": r[0], "name": r[1], "face_uri": r[2]}
        for r in ch.query(
            f"SELECT character_id, name, face_uri FROM {DB}.characters FINAL "
            f"WHERE production_id = ("
            f"  SELECT any(production_id) FROM {DB}.scenes WHERE scene_id = %(s)s)",
            parameters={"s": scene_id},
        ).result_rows
    }

    in_take: dict[str, list[dict]] = {}
    for r in ch.query(
        f"""
        SELECT take_id, character_id, prominence FROM {DB}.take_characters
        WHERE scene_id = %(s)s
        """,
        parameters={"s": scene_id},
    ).result_rows:
        who = people.get(r[1], {"character_id": r[1], "name": r[1], "face_uri": ""})
        in_take.setdefault(r[0], []).append({**who, "prominence": r[2]})

    faults: dict[str, list[dict]] = {}
    for r in ch.query(
        f"""
        SELECT take_id, severity, category, what, where_in_frame, at_seconds
        FROM {DB}.take_problems WHERE scene_id = %(s)s
        ORDER BY take_id, severity
        """,
        parameters={"s": scene_id},
    ).result_rows:
        faults.setdefault(r[0], []).append(
            {"severity": r[1], "category": r[2], "what": r[3],
             "where": r[4], "at_seconds": float(r[5] or 0)})

    takes = []
    for r in ch.query(
        f"""
        SELECT a.take_id, a.setup_id, a.shot_size, a.movement,
               t.duration_s, t.circled
        FROM {DB}.take_analysis AS a
        LEFT JOIN {DB}.takes AS t USING (take_id)
        WHERE a.scene_id = %(s)s
        ORDER BY a.setup_id, a.take_id
        """,
        parameters={"s": scene_id},
    ).result_rows:
        marks = faults.get(r[0], [])
        takes.append({
            "take_id": r[0], "setup_id": r[1],
            "shot_size": r[2], "shot_size_plain": SHOT_SIZES.get(r[2], r[2]),
            "movement": r[3], "movement_plain": MOVEMENTS.get(r[3], r[3]),
            "seconds": float(r[4] or 0), "circled": bool(r[5]),
            "playable": clip_path(r[0]).exists(),
            "characters": in_take.get(r[0], []),
            "gives": gives.get(r[0], []),
            "problems": marks,
            "usable": not any(m["severity"] == "blocking" for m in marks),
        })

    return {
        "scene": {"scene_id": s[0], "place": s[1].replace("_", " "),
                  "int_ext": s[2], "day_night": s[3], "synopsis": s[4],
                  "takes": len(takes),
                  "positions": len({t["setup_id"] for t in takes})},
        "bands": cc.BANDS, "band_label": cc.BAND_LABEL,
        "characters": cc.as_json(rows),
        "scene_shots": cc.scene_as_json(shots),
        "summary": summary,
        "takes": takes,
    }
