"""Takes, and putting right where one was split from another.

Where a take begins is a judgment, and the agent gets it wrong in both
directions on real camera-card footage. It reads a whip pan through a struggle
as a cut, and it reads straight past a reset where the crew go again without
slating. Measured against a script supervisor's own count of one ten-minute
card, it has landed anywhere from a third under to two thirds over.

Nobody watching the footage is in any doubt. So rather than keep tuning the
judgment, the person who can see it says so, once, and it stays said. The same
answer the interface already gives for two scenes that are one place and two
faces that are one person.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from agents.intake import clip_path, joined_path
from api import workspace as ws
from api.shared import DB, client

router = APIRouter()


class Joining(BaseModel):
    take_ids: list[str]


def concat(clips: list[Path], out: Path) -> bool:
    """Join clips end to end without re-encoding them.

    They were cut from one file moments apart, so they share a codec and a
    frame size and the stream copy is exact. Re-encoding would cost quality
    for nothing.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as listing:
        for clip in clips:
            listing.write(f"file '{clip.as_posix()}'\n")
        manifest = listing.name

    try:
        done = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "concat", "-safe", "0", "-i", manifest,
             "-c", "copy", str(out)],
            capture_output=True,
        )
        return done.returncode == 0 and out.exists()
    finally:
        Path(manifest).unlink(missing_ok=True)


@router.post("/api/takes/join")
def join_takes(body: Joining, request: Request, response: Response):
    """Two or more takes that are really one, joined back together.

    The clips are put end to end and the surviving take is re-watched, because
    everything hanging off it — who is in it, what framing it gives them, what
    stops it being used — was decided about a fragment and has to be decided
    again about the whole.
    """
    ch = client()
    mine = ws.writable(request, response)

    if len(body.take_ids) < 2:
        raise HTTPException(status_code=400, detail="Give at least two takes")

    rows = ch.query(
        f"""
        SELECT take_id, scene_id, setup_id, take_no, duration_s, shoot_day,
               camera_roll, clip_name, proxy_uri
        FROM {DB}.takes
        WHERE production_id = %(p)s AND take_id IN %(t)s
        ORDER BY take_id
        """,
        parameters={"p": mine, "t": tuple(body.take_ids)},
    ).result_rows
    if len(rows) != len(body.take_ids):
        raise HTTPException(status_code=404, detail="No such take")

    if len({r[1] for r in rows}) > 1:
        raise HTTPException(
            status_code=400,
            detail="Those takes are in different scenes. Join the scenes first.")


    clips = [clip_path(r[0], mine) for r in rows]
    missing = [r[0] for r, c in zip(rows, clips) if not c.exists()]
    if missing:
        raise HTTPException(status_code=409,
                            detail=f"No footage here for {', '.join(missing)}")

    # Ordered by take id, which is the order they came off the card, so the
    # clips go back together the way they were shot. Take numbers could not do
    # it: they run within a setup, and a take split by mistake often lands
    # across two of them — which is the case being corrected here.
    keep = rows[0]
    setup = keep[2]

    # Written under this workspace, not over the clips it read. A day copied
    # from another one shares both take ids and footage, so deleting the
    # sources here took the footage out from under the day it was copied from
    # — its takes played until somebody joined two in the copy.
    joined = joined_path(mine, keep[0])
    joined.parent.mkdir(parents=True, exist_ok=True)
    if not concat(clips, joined):
        raise HTTPException(status_code=500, detail="Could not join the clips")

    seconds = float(sum(r[4] for r in rows))
    gone = [r[0] for r in rows[1:]]

    for table in ("takes", "take_analysis", "take_characters", "take_problems"):
        ch.command(
            f"ALTER TABLE {DB}.{table} DELETE "
            f"WHERE production_id = %(p)s AND take_id IN %(t)s",
            parameters={"p": mine, "t": tuple(gone)},
            settings={"mutations_sync": 2},
        )

    # The survivor is re-inserted with the length of the whole thing. The old
    # row is a ReplacingMergeTree version we cannot update in place.
    ch.command(
        f"ALTER TABLE {DB}.takes DELETE "
        f"WHERE production_id = %(p)s AND take_id = %(t)s",
        parameters={"p": mine, "t": keep[0]},
        settings={"mutations_sync": 2},
    )
    ch.insert("takes", [[
        mine, keep[5], keep[1], keep[2], keep[3], keep[0],
        keep[6], keep[7], "10:00:00:00", "10:00:00:00",
        seconds, 0.0, 0.0, "none", 800, 24.0, "SONY_F65", "complete", 0,
        0.0, keep[8],
    ]], column_names=[
        "production_id", "shoot_day", "scene_id", "setup_id", "take_no",
        "take_id", "camera_roll", "clip_name", "tc_start", "tc_end",
        "duration_s", "lens_mm", "t_stop", "nd", "iso", "fps", "camera_body",
        "status", "circled", "slate_confidence", "proxy_uri"])

    return {"ok": True, "take_id": keep[0], "joined": len(rows),
            "seconds": round(seconds, 1), "removed": gone,
            "scene_id": keep[1]}

class Moving(BaseModel):
    take_ids: list[str]
    scene_id: str


@router.post("/api/takes/move")
def move_takes(body: Moving, request: Request, response: Response):
    """Takes filed under the wrong scene, put where they belong.

    Scenes are grouped by what the place looks like, and two grey industrial
    interiors look alike. When that goes wrong it is usually a few takes at
    the end of one scene that were really the start of another, which is a
    smaller thing than the two scenes being one — so it is a separate control
    from joining scenes.
    """
    ch = client()
    mine = ws.writable(request, response)
    target = ws.scene_for(ch, mine, body.scene_id)

    if not body.take_ids:
        raise HTTPException(status_code=400, detail="Give at least one take")

    here = ch.query(
        f"SELECT scene_id, location_id FROM {DB}.scenes "
        f"WHERE production_id = %(p)s AND scene_id = %(s)s",
        parameters={"p": mine, "s": target},
    ).result_rows
    if not here:
        raise HTTPException(status_code=404, detail="No such scene")

    # The camera position moves with the takes. A setup belongs to one scene,
    # so leaving it behind would put a scene's takes under another's setup and
    # the schedule would price a shot that is not there.
    setups = [r[0] for r in ch.query(
        f"SELECT DISTINCT setup_id FROM {DB}.takes "
        f"WHERE production_id = %(p)s AND take_id IN %(t)s",
        parameters={"p": mine, "t": tuple(body.take_ids)},
    ).result_rows]

    # Read, rewrite, re-insert, delete. scene_id is part of what a take is
    # sorted by, so ClickHouse will not update it in place — the same reason
    # joining two scenes moves rows rather than pointing them somewhere else.
    def relocate(table: str, where: str, params: dict) -> None:
        rows = ch.query(f"SELECT * FROM {DB}.{table} WHERE {where}",
                        parameters=params)
        if not rows.result_rows:
            return
        names = list(rows.column_names)
        at = names.index("scene_id")
        ch.insert(
            table,
            [[target if i == at else v for i, v in enumerate(row)]
             for row in rows.result_rows],
            column_names=names,
        )
        # Everything but the scene is unchanged, so a delete keyed on the take
        # matches the rows just written as readily as the ones being replaced.
        # It did, and took both.
        ch.command(
            f"ALTER TABLE {DB}.{table} DELETE "
            f"WHERE ({where}) AND scene_id != %(to)s",
            parameters={**params, "to": target},
            settings={"mutations_sync": 2},
        )

    picked = {"p": mine, "t": tuple(body.take_ids)}
    for table in ("takes", "take_analysis", "take_characters", "take_problems"):
        relocate(table, "production_id = %(p)s AND take_id IN %(t)s", picked)

    for setup_id in setups:
        left = ch.query(
            f"SELECT count() FROM {DB}.takes WHERE production_id = %(p)s "
            f"AND setup_id = %(u)s AND scene_id != %(to)s",
            parameters={"p": mine, "u": setup_id, "to": target},
        ).result_rows[0][0]
        if left:
            continue        # the setup still has takes in the old scene
        relocate("setups", "production_id = %(p)s AND setup_id = %(u)s",
                 {"p": mine, "u": setup_id})

    return {"ok": True, "moved": len(body.take_ids), "scene_id": target,
            "place": here[0][1].replace("_", " ")}
