"""Who is in the footage, and what is still needed of them.

Faces are found by looking, and looking fails, an actor lit from behind, a
character whose face is inside a hood, a chase where nobody turns to camera. So
everything here can be corrected by hand: two faces merged into one person,
someone named, or someone simply declared to be in a take.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel


class BandSetting(BaseModel):
    required: bool
    recover_cost_usd: int | None = None

from api import workspace as ws
from api.shared import DB, DONE_BEFORE_REPLYING, client
from core import character_coverage as cc

router = APIRouter()


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
    mine = ws.writable(request, response)
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

    Face matching splits people it should not, a back-of-head shot and a
    close-up genuinely do not look alike. Rather than pretend otherwise, the
    AD says so once and every take moves across.
    """
    if character_id == other_id:
        return {"ok": False, "reason": "same person"}

    ch = client()
    mine = ws.workspace_id(request, response)
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
        parameters={"d": drop[0]}, settings=DONE_BEFORE_REPLYING,
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
        parameters={"d": drop[0]}, settings=DONE_BEFORE_REPLYING,
    )

    needs = ch.query(
        f"""
        SELECT scene_id, shot_type, required, recover_cost_usd
        FROM {DB}.character_requirements FINAL WHERE character_id = %(d)s
        """,
        parameters={"d": drop[0]}, settings=DONE_BEFORE_REPLYING,
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
        parameters={"d": drop[0]}, settings=DONE_BEFORE_REPLYING,
    )
    ch.command(
        f"ALTER TABLE {DB}.characters UPDATE appearances = %(n)s "
        f"WHERE character_id = %(k)s",
        parameters={"n": keep[2] + drop[2], "k": keep[0]},
        settings=DONE_BEFORE_REPLYING,
    )
    ch.command(
        f"ALTER TABLE {DB}.characters DELETE WHERE character_id = %(d)s",
        parameters={"d": drop[0]}, settings=DONE_BEFORE_REPLYING,
    )
    return {"ok": True, "kept": keep[0], "name": keep[1], "removed": drop[0]}


@router.put("/api/scenes/{scene_id}/need/{character_id}/{band}")
def set_need(scene_id: str, character_id: str, band: str, body: BandSetting,
             request: Request, response: Response):
    """Tick a shot on or off for one person. The call reruns from this."""
    ch = client()
    mine = ws.writable(request, response)
    scene_id = ws.scene_for(ch, mine, scene_id)
    character_id = ws.character_for(ch, mine, character_id)

    # Zero means nobody has said, so the read works it out from the crew
    # on the day. Storing the default instead froze it: rows written
    # under a fifty-person unit still quoted fifty-person money after the
    # production was cut to three.
    cost = body.recover_cost_usd or 0
    ch.insert(
        "character_requirements",
        [[scene_id, character_id, band, int(body.required), cost, datetime.now()]],
        column_names=["scene_id", "character_id", "shot_type", "required",
                      "recover_cost_usd", "updated_at"],
    )
    return {"ok": True, "character_id": character_id, "band": band,
            "required": body.required}


class InTake(BaseModel):
    character_id: str = ""
    name: str = ""


@router.get("/api/people")
def people(request: Request, response: Response):
    """Everyone this production knows about, for saying who is in a take."""
    ch = client()
    mine = ws.read_from(ch, ws.workspace_id(request, response))
    rows = ch.query(
        f"""
        SELECT character_id, name, face_uri, appearances
        FROM {DB}.characters FINAL WHERE production_id = %(p)s
        ORDER BY appearances DESC
        """,
        parameters={"p": mine},
    ).result_rows
    return [{"character_id": r[0], "name": r[1], "face_uri": r[2],
             "appearances": r[3]} for r in rows]


@router.post("/api/takes/{take_id}/who")
def someone_is_in(take_id: str, body: InTake,
                  request: Request, response: Response):
    """Say who is in a take, when the footage did not make it obvious.

Faces are found by looking, and looking fails: an actor lit from behind, a
    chase where nobody turns to camera, a character whose face is inside a
    hood.
    """
    ch = client()
    mine = ws.writable(request, response)

    where = ch.query(
        f"SELECT production_id, scene_id, setup_id FROM {DB}.take_analysis "
        f"WHERE take_id = %(t)s LIMIT 1",
        parameters={"t": take_id},
    ).result_rows
    if not where:
        raise HTTPException(status_code=404, detail="No such take")
    production_id, scene_id, setup_id = where[0]

    character_id = body.character_id
    if not character_id:
        name = body.name.strip()[:60]
        if not name:
            raise HTTPException(status_code=400,
                                detail="Pick someone, or give a name")
        character_id = f"char_{uuid.uuid4().hex[:8]}"
        # No face and no embedding: this person was never seen by the matcher,
        # and a zero vector is skipped by it rather than matching everything.
        ch.insert(
            "characters",
            [[mine, character_id, name, "", [0.0] * 1408, "added by hand",
              take_id, 0, datetime.now()]],
            column_names=["production_id", "character_id", "name", "face_uri",
                          "embedding", "description", "first_take_id",
                          "appearances", "created_at"],
        )

    already = ch.query(
        f"SELECT count() FROM {DB}.take_characters "
        f"WHERE take_id = %(t)s AND character_id = %(c)s",
        parameters={"t": take_id, "c": character_id},
    ).result_rows[0][0]
    if already:
        return {"ok": True, "character_id": character_id, "already": True}

    ch.insert(
        "take_characters",
        [[production_id, scene_id, setup_id, take_id, character_id,
          1.0, [0.0, 0.0, 0.0, 0.0], "foreground", "said_so"]],
        column_names=["production_id", "scene_id", "setup_id", "take_id",
                      "character_id", "confidence", "bbox", "prominence",
                      "matched_by"],
    )
    ch.command(
        f"ALTER TABLE {DB}.characters UPDATE appearances = appearances + 1 "
        f"WHERE production_id = %(p)s AND character_id = %(c)s",
        parameters={"p": mine, "c": character_id},
    )
    return {"ok": True, "character_id": character_id}


@router.delete("/api/takes/{take_id}/who/{character_id}")
def not_in_take(take_id: str, character_id: str,
                request: Request, response: Response):
    """They are not in it after all."""
    ch = client()
    ws.writable(request, response)
    ch.command(
        f"ALTER TABLE {DB}.take_characters DELETE "
        f"WHERE take_id = %(t)s AND character_id = %(c)s",
        parameters={"t": take_id, "c": character_id},
        settings=DONE_BEFORE_REPLYING,
    )
    return {"ok": True}
