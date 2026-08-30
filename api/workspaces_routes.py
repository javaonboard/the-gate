"""Choosing a workspace, rather than being given one.

The previous arrangement handed every visitor a workspace silently and kept it
in a cookie.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from api import workspace as ws
from core.coverage import connect

router = APIRouter()

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")

_client = None


def client():
    global _client
    if _client is None:
        _client = connect()
    return _client


def ensure_table() -> None:
    client().command(f"""
        CREATE TABLE IF NOT EXISTS {DB}.workspaces
        (
            workspace_id  String,
            label         String,
            kind          LowCardinality(String),   -- empty | copy | demo
            created_at    DateTime,
            last_used     DateTime
        )
        ENGINE = ReplacingMergeTree(last_used)
        ORDER BY (workspace_id)
    """)


def workspace_label(workspace_id: str) -> str | None:
    """What to call the day you are in, or None if it is not there any more.

    A browser can hold a cookie for a day that has since been deleted, from
    another tab, or another person. Treating that as a workspace left the
    interface open on nothing at all, with no way back to the chooser.
    """
    if workspace_id == ws.DEMO:
        return "The demo"
    ensure_table()
    rows = client().query(
        f"SELECT label FROM {DB}.workspaces FINAL WHERE workspace_id = %(w)s",
        parameters={"w": workspace_id},
    ).result_rows
    return rows[0][0] if rows else None


class NewWorkspace(BaseModel):
    label: str = ""
    start_from: str = "empty"        # empty | demo
    crew: str = ""                   # which kind of shoot this is


@router.get("/api/workspaces")
def list_workspaces(request: Request):
    """Everything on this machine, newest first, with how much is in it."""
    ensure_table()
    ch = client()

    rows = ch.query(
        f"""
        SELECT w.workspace_id, w.label, w.kind, w.created_at, w.last_used,
               ifNull(s.n, 0) AS scenes, ifNull(t.n, 0) AS takes
        FROM {DB}.workspaces AS w FINAL
        LEFT JOIN (
            SELECT production_id, count() AS n FROM {DB}.scenes
            GROUP BY production_id
        ) AS s ON s.production_id = w.workspace_id
        LEFT JOIN (
            SELECT production_id, count() AS n FROM {DB}.takes GROUP BY production_id
        ) AS t ON t.production_id = w.workspace_id
        ORDER BY w.last_used DESC
        """
    ).result_rows

    demo = ch.query(
        f"""
        SELECT
          (SELECT count() FROM {DB}.scenes WHERE production_id = %(d)s),
          (SELECT count() FROM {DB}.takes WHERE production_id = %(d)s)
        """,
        parameters={"d": ws.DEMO},
    ).result_rows[0]

    # Named after what is actually in it. It used to say "Tears of Steel",
    # which stopped being true the moment the demo was rebuilt on other
    # footage and nobody noticed.
    places = [
        r[0].replace("_", " ") for r in ch.query(
            f"SELECT location_id FROM {DB}.scenes "
            f"WHERE production_id = %(d)s AND location_id != 'nothing_yet' "
            f"ORDER BY scene_id LIMIT 2",
            parameters={"d": ws.DEMO},
        ).result_rows
    ]
    demo_label = ("A day already shot: " + " and ".join(places)
                  if places else "The demo")

    current = request.cookies.get(ws.COOKIE, "")

    return {
        "current": current,
        "demo": {"workspace_id": ws.DEMO, "label": demo_label,
                 "kind": "demo", "scenes": demo[0], "takes": demo[1]},
        "workspaces": [
            {"workspace_id": r[0], "label": r[1] or "Untitled", "kind": r[2],
             "created_at": r[3].isoformat(), "last_used": r[4].isoformat(),
             "scenes": r[5], "takes": r[6]}
            for r in rows
        ],
    }


@router.post("/api/workspaces")
def create_workspace(body: NewWorkspace, response: Response):
    """Start a new one, empty, or as a copy of the demo."""
    ensure_table()
    ch = client()

    workspace = ws.new_workspace_id()
    label = body.label.strip()[:60] or datetime.now().strftime("Shoot day %d %b, %H:%M")
    kind = "copy" if body.start_from == "demo" else "empty"

    if kind == "copy":
        ws.fork(ch, workspace)

    now = datetime.now()
    ch.insert("workspaces", [[workspace, label, kind, now, now]],
              column_names=["workspace_id", "label", "kind", "created_at",
                            "last_used"])

    # Said up front, because every figure the day produces is a headcount
    # times a rate. A two-person shoot and a studio unit are the same product
    # with a very different multiplier.
    if body.crew:
        from core import crew as crew_tiers
        from core.crew import crew_table

        tier = crew_tiers.BY_KEY.get(body.crew)
        if tier:
            crew_table()
            ch.insert("crew_plan", [[
                workspace, tier.key, json.dumps(tier.departments), tier.cast,
                0, tier.crew_rate, tier.cast_rate, now,
            ]], column_names=["production_id", "tier", "departments", "cast",
                              "minors", "crew_rate", "cast_rate", "updated_at"])

    response.set_cookie(ws.COOKIE, workspace, max_age=ws.COOKIE_MAX_AGE,
                        httponly=True, samesite="lax")
    return {"workspace_id": workspace, "label": label, "kind": kind}


@router.post("/api/workspaces/{workspace_id}/open")
def open_workspace(workspace_id: str, response: Response):
    """Carry on with one you already have, or look at the demo."""
    ensure_table()
    ch = client()
    if workspace_id != ws.DEMO:
        known = ch.query(
            f"SELECT label, kind, created_at FROM {DB}.workspaces FINAL "
            f"WHERE workspace_id = %(w)s",
            parameters={"w": workspace_id},
        ).result_rows
        if known:
            label, kind, created = known[0]
            ch.insert("workspaces",
                      [[workspace_id, label, kind, created, datetime.now()]],
                      column_names=["workspace_id", "label", "kind",
                                    "created_at", "last_used"])
    response.set_cookie(ws.COOKIE, workspace_id, max_age=ws.COOKIE_MAX_AGE,
                        httponly=True, samesite="lax")
    return {"workspace_id": workspace_id}


@router.delete("/api/workspaces/{workspace_id}")
def delete_workspace(workspace_id: str):
    """Throw one away entirely. The demo cannot be deleted.

Twelve tables hold rows for a day, and waiting for every one of those
    mutations to finish took about thirty seconds, during which the day sat
    there looking undeleted and could be deleted again.
    """
    if workspace_id == ws.DEMO:
        return {"ok": False, "reason": "the demo stays"}

    ensure_table()
    ch = client()

    # the one that makes it disappear
    ch.command(
        f"ALTER TABLE {DB}.workspaces DELETE WHERE workspace_id = %(w)s",
        parameters={"w": workspace_id}, settings={"mutations_sync": 2},
    )

    # and the rows behind it, which nobody can get to any more
    for table in ("scenes", "setups", "takes", "take_analysis",
                  "take_characters", "characters", "shoot_days", "crew_hours",
                  "take_problems", "production_world", "crew_plan"):
        try:
            ch.command(
                f"ALTER TABLE {DB}.{table} DELETE WHERE production_id = %(w)s",
                parameters={"w": workspace_id},
            )
        except Exception:
            # a table that does not exist yet in this deployment is not a
            pass
    try:
        ch.command(
            f"ALTER TABLE {DB}.character_requirements DELETE "
            f"WHERE startsWith(scene_id, %(w)s)",
            parameters={"w": workspace_id},
        )
    except Exception:
        pass

    return {"ok": True, "deleted": workspace_id}


@router.post("/api/workspaces/leave")
def leave(response: Response):
    """Go back to the chooser."""
    response.delete_cookie(ws.COOKIE)
    return {"ok": True}
