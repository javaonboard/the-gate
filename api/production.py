"""The day itself: when it starts, who is on it, what world it is set in.

Everything here is a fact about the production rather than about the footage:
what somebody tells the system, which the rest of it is then judged against.
The crew is the multiplier on every figure the day produces; the world is what
makes an anachronism an anachronism.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from api import workspace as ws
from api.shared import DB, DONE_BEFORE_REPLYING, client
from core import crew
from core.crew import crew_of, crew_table

router = APIRouter()


class World(BaseModel):
    period: str = ""
    setting: str = ""
    notes: str = ""


def plan_for(ch, production_id: str) -> tuple[datetime, datetime, date]:
    """When this day starts and when it is meant to end.

    One reader for both the banner and the simulation. They used to work it out
    separately — the banner from this table, the gate from a call time written
    into the code — so the clock on screen and the clock being simulated were
    hours apart, and "the latest we can finish" was derived from a time nobody
    had set.
    """
    rows = ch.query(
        f"""
        SELECT call_time, sunset, shoot_day
        FROM {DB}.shoot_days WHERE production_id = %(p)s
        ORDER BY shoot_day DESC LIMIT 1
        """,
        parameters={"p": production_id},
        # Read your own write. The day is set and read back a moment later,
        # and a replica that had not caught up returned nothing — which this
        # function then quietly answered with a seven o'clock default, so a
        # call time you had just typed came back as one you had not.
        settings={"select_sequential_consistency": 1},
    ).result_rows
    if rows:
        return rows[0][0], rows[0][1], rows[0][2]

    # Nothing set yet: a plain twelve-hour day starting at seven.
    day = date.today()
    call_time = datetime.combine(day, datetime.min.time()) + timedelta(hours=7)
    return call_time, call_time + timedelta(hours=12), day


@router.get("/api/day")
def get_day(request: Request, response: Response):
    """When the day starts and ends.

    Taken from the shoot day on record rather than typed in, a production
    already knows its call time. Editable, because plans change.
    """
    ch = client()
    mine = ws.read_from(ch, ws.workspace_id(request, response))
    call_time, sunset, day = plan_for(ch, mine)

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
    mine = ws.writable(request, response)

    call_time = datetime.fromisoformat(body.call_time)
    wrap_time = datetime.fromisoformat(body.wrap_time)
    day = call_time.date()

    ch.command(
        f"ALTER TABLE {DB}.shoot_days DELETE WHERE production_id = %(p)s",
        # Waited on one replica, so the delete could land after the row that
        # replaces it and eat it. The day then read back as the seven o'clock
        # default, and setting a call time looked like it had been refused.
        parameters={"p": mine}, settings=DONE_BEFORE_REPLYING,
    )
    scenes = [r[0] for r in ch.query(
        f"SELECT scene_id FROM {DB}.scenes WHERE production_id = %(p)s",
        parameters={"p": mine},
    ).result_rows]

    # Stored as the clock on the wall, which is the only clock a call sheet
    # has. Handed a naive datetime the driver reads it as local and converts
    # it to the server's UTC, so a midday call went in as five in the evening
    # — and the simulation then ran the wrong five hours of the day. Marking
    # it UTC makes the conversion a no-op and the time comes back as typed.
    at_utc = [t.replace(tzinfo=timezone.utc) if t else None
              for t in (call_time, wrap_time, call_time - timedelta(minutes=45))]

    ch.insert("shoot_days", [[
        mine, day, "main", "set", at_utc[0], None, at_utc[2], at_utc[1], scenes,
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
    mine = ws.writable(request, response)
    ch.insert("production_world", [[
        mine, body.period.strip()[:200], body.setting.strip()[:200],
        body.notes.strip()[:400], datetime.now(),
    ]], column_names=["production_id", "period", "setting", "notes",
                      "updated_at"])
    return {"ok": True, "period": body.period, "setting": body.setting}


@router.delete("/api/workspace")
def clear_workspace(request: Request, response: Response):
    """Throw everything away and start from an empty day.

    Only touches this visitor's copy. The seeded demo is left alone, so the
    next person still arrives at something.
    """
    ch = client()
    mine = ws.writable(request, response)

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

    return {"cleared": mine}


class CrewPlan(BaseModel):
    tier: str = ""
    departments: dict[str, int] | None = None
    cast: int | None = None
    minors: int | None = None
    crew_rate: float | None = None
    cast_rate: float | None = None








@router.get("/api/crew")
def get_crew(request: Request, response: Response):
    """The crew this day is costed against."""
    ch = client()
    mine = ws.read_from(ch, ws.workspace_id(request, response))
    plan = crew_of(mine)
    people = crew.as_people(plan["departments"], plan["cast"], plan["minors"],
                            plan["crew_rate"], plan["cast_rate"])
    return {
        **plan,
        "crew_size": sum(plan["departments"].values()),
        "on_the_clock": len(people),
        "departments_labelled": [
            {"key": k, "label": label, "count": plan["departments"].get(k, 0)}
            for k, label in crew.DEPARTMENTS
        ],
        "tiers": crew.tier_json(),
    }


@router.put("/api/crew")
def set_crew(body: CrewPlan, request: Request, response: Response):
    """Say who is actually on this day.

    Two people making something for the web and a studio unit are the same
    product with a different multiplier, and until somebody says which, every
    figure is a guess dressed as a fact.
    """
    ch = client()
    mine = ws.writable(request, response)
    crew_table()

    now = crew_of(mine)
    base = crew.BY_KEY.get(body.tier) if body.tier else None
    plan = {
        "tier": body.tier or now["tier"],
        "departments": (body.departments if body.departments is not None
                        else dict(base.departments) if base else now["departments"]),
        "cast": (body.cast if body.cast is not None
                 else base.cast if base else now["cast"]),
        "minors": body.minors if body.minors is not None else now["minors"],
        "crew_rate": (body.crew_rate if body.crew_rate is not None
                      else base.crew_rate if base else now["crew_rate"]),
        "cast_rate": (body.cast_rate if body.cast_rate is not None
                      else base.cast_rate if base else now["cast_rate"]),
    }

    ch.insert("crew_plan", [[
        mine, plan["tier"], json.dumps(plan["departments"]), int(plan["cast"]),
        int(plan["minors"]), float(plan["crew_rate"]), float(plan["cast_rate"]),
        datetime.now(),
    ]], column_names=["production_id", "tier", "departments", "cast", "minors",
                      "crew_rate", "cast_rate", "updated_at"])
    return {"ok": True, **plan,
            "crew_size": sum(plan["departments"].values())}
