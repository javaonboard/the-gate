"""THE GATE — backend.

Serves the gate call to the interface, streams live crew activity over SSE, and
receives Parallel Monitor webhooks so the world can interrupt the shoot day.

    uvicorn api.main:app --reload --port 8080
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents.orchestrator import Trigger, run_gate
from api.casting_routes import router as casting_router
from api.events import bus, sse
from api.labels import AGENTS, GLOSSARY, MOVEMENTS, SHOT_SIZES, person_label
from core.coverage import connect
from core.union_rules import Person

load_dotenv()

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")
DEFAULT_SCENE = "prod_now_sc001"

# How long a single SSE connection is held before the client is asked to
# reconnect. EventSource reconnects on its own, and a bounded stream means the
# server can actually shut down.
STREAM_MAX_SECONDS = 900

app = FastAPI(title="THE GATE")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(casting_router)

# Cropped faces, served straight to the interface.
FACES_DIR = Path(__file__).resolve().parent / "static" / "faces"
FACES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/faces", StaticFiles(directory=str(FACES_DIR)), name="faces")

_local = threading.local()


def client():
    """One ClickHouse client per thread.

    FastAPI runs sync endpoints in a thread pool, so a single shared client gets
    concurrent queries. Sessions are also disabled in connect(); this is the
    belt to that pair of braces.
    """
    existing = getattr(_local, "client", None)
    if existing is None:
        existing = connect()
        _local.client = existing
    return existing


def demo_crew() -> list[Person]:
    return (
        [Person(f"camera_{i:02d}", "camera") for i in range(18)]
        + [Person(f"cast_{i}", "cast", kind="performer", hourly_rate=180.0)
           for i in range(3)]
    )


# --- the call ---------------------------------------------------------------

async def _do_gate(run, scene_id: str, hours_in: float, use_agent: bool,
                   trigger: str):
    call = datetime(2026, 8, 16, 7, 0)
    try:
        result = await run_gate(
            scene_id,
            now=call + timedelta(hours=hours_in),
            call=call,
            next_call=call + timedelta(days=1, hours=1),
            run=run,
            trigger=Trigger(trigger),
            use_agent=use_agent,
        )
        payload = {"spoken": result["spoken"], **serialise(result["report"])}
        run.result = payload
        run.publish("orchestrator", "result", result["spoken"], payload)
    except Exception as exc:
        run.publish("orchestrator", "error", str(exc))
    finally:
        run.finish()


@app.post("/api/runs")
async def start_run(scene_id: str = DEFAULT_SCENE, hours_in: float = 9.0,
                    use_agent: bool = True, trigger: str = "schedule"):
    """Start a check and return immediately.

    The interface needs the run id before the work begins, otherwise it opens
    the stream after everything has already happened and the crew appears to
    finish instantly. The call itself arrives as a `result` event on the stream.
    """
    run = bus.start(scene_id)
    asyncio.create_task(_do_gate(run, scene_id, hours_in, use_agent, trigger))
    return {"run_id": run.run_id, "scene_id": scene_id}


@app.get("/api/runs/{run_id}/result")
def run_result(run_id: str):
    run = bus.get(run_id)
    if run is None:
        return {"error": "no such run"}
    return {"finished": run.finished, "result": getattr(run, "result", None)}


@app.get("/api/gate/{scene_id}")
async def gate(scene_id: str = DEFAULT_SCENE, hours_in: float = 9.0,
               use_agent: bool = True, trigger: str = "schedule"):
    """Run the crew and wait for the call. Kept for scripts and curl.

    Set use_agent=false to skip the language models and return the computed call
    alone — useful when demonstrating the numbers do not depend on them.
    """
    run = bus.start(scene_id)
    await _do_gate(run, scene_id, hours_in, use_agent, trigger)
    return {"run_id": run.run_id, **(getattr(run, "result", None) or {})}


def serialise(report) -> dict[str, Any]:
    b = report.baseline
    return {
        "scene_id": report.scene_id,
        "now": report.now.isoformat(),
        "verdict": report.verdict,
        "go": report.go,
        "summary": report.summary(),
        "coverage": {
            "completeness": round(report.coverage.completeness, 3),
            "people": report.coverage.people,
            "have": report.coverage.summary["have"],
            "required": report.coverage.summary["required"],
            "exposure_usd": report.coverage.exposure_usd,
            "missing": [
                {"character_id": m.character_id, "person": m.person,
                 "band": m.band, "label": m.label,
                 "describe": m.describe,
                 "recover_cost_usd": m.recover_cost_usd}
                for m in report.coverage.missing()
            ],
        },
        "day": {
            "p_make_the_day": round(b.p_make_the_day, 4),
            "hard_stop": b.hard_stop.isoformat(),
            "median_wrap": b.median_wrap.isoformat(),
            "p90_wrap": b.p90_wrap.isoformat(),
            "expected_penalty_usd": round(b.expected_penalty_usd),
            "setups_remaining": len(b.setups),
            "trials": b.trials,
        },
        "options": [
            {
                "shot_type": o.requirement.label,
                "subject": o.requirement.person,
                "describe": o.requirement.describe,
                "shoot_now_usd": round(o.shoot_now_usd),
                "recover_later_usd": o.recover_later_usd,
                "saving_usd": round(o.saving_usd),
                "p_make_day_after": round(o.p_make_day_after, 4),
                "worth_it": o.worth_it,
                "verdict": o.verdict,
            }
            for o in sorted(report.options, key=lambda o: -o.saving_usd)
        ],
    }


# --- live activity ----------------------------------------------------------

@app.get("/api/runs/{run_id}/stream")
async def stream(run_id: str, request: Request):
    """Server-Sent Events. The browser holds this open and we push down it."""
    run = bus.get(run_id)
    if run is None:
        return StreamingResponse(iter([": no such run\n\n"]),
                                 media_type="text/event-stream")

    async def gen():
        q = run.subscribe()
        # Give up eventually. An open stream otherwise blocks uvicorn's
        # shutdown, which makes Ctrl-C appear to hang.
        deadline = asyncio.get_event_loop().time() + STREAM_MAX_SECONDS
        try:
            while asyncio.get_event_loop().time() < deadline:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=5.0)
                    yield sse(event)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    if run.finished:
                        break
        except asyncio.CancelledError:
            pass
        finally:
            run.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/runs/{run_id}")
def run_history(run_id: str):
    run = bus.get(run_id)
    if run is None:
        return {"error": "no such run"}
    return {
        "run_id": run.run_id,
        "scene_id": run.scene_id,
        "finished": run.finished,
        "events": [e.to_dict() for e in run.history],
    }


# --- the scene: what an editor will need ------------------------------------

SHOT_TYPES = ["master", "single", "ots", "insert", "reaction", "establishing", "plate"]


class NewRequirement(BaseModel):
    shot_type: str
    subject: str = "-"
    priority: int = 1
    recover_cost_usd: int = 20000
    is_vfx_plate: bool = False


@app.get("/api/scenes/{scene_id}/requirements")
def get_requirements(scene_id: str):
    """What this scene needs. Generated as a starting point, then edited here."""
    rows = client().query(
        f"""
        SELECT req_id, shot_type, subject, priority, recover_cost_usd, is_vfx_plate
        FROM {DB}.scene_requirements WHERE scene_id = %(s)s
        ORDER BY priority, req_id
        """,
        parameters={"s": scene_id},
    ).result_rows
    return {
        "scene_id": scene_id,
        "shot_types": [
            {"value": t, "label": GLOSSARY.get(t, {}).get("short", t)}
            for t in SHOT_TYPES
        ],
        "requirements": [
            {"req_id": r[0], "shot_type": r[1], "subject": r[2], "priority": r[3],
             "recover_cost_usd": r[4], "is_vfx_plate": bool(r[5]),
             "plain": GLOSSARY.get(r[1], {}).get("short", r[1])}
            for r in rows
        ],
    }


@app.post("/api/scenes/{scene_id}/requirements")
def add_requirement(scene_id: str, req: NewRequirement):
    req_id = f"{scene_id}_u{uuid.uuid4().hex[:6]}"
    client().insert(
        "scene_requirements",
        [[scene_id, req_id, req.shot_type, req.subject, req.priority,
          req.recover_cost_usd, int(req.is_vfx_plate)]],
        column_names=["scene_id", "req_id", "shot_type", "subject", "priority",
                      "recover_cost_usd", "is_vfx_plate"],
    )
    return {"req_id": req_id}


@app.delete("/api/scenes/{scene_id}/requirements/{req_id}")
def delete_requirement(scene_id: str, req_id: str):
    client().command(
        f"ALTER TABLE {DB}.scene_requirements DELETE "
        f"WHERE scene_id = %(s)s AND req_id = %(r)s",
        parameters={"s": scene_id, "r": req_id},
    )
    return {"deleted": req_id}


@app.get("/api/scenes/{scene_id}/takes")
def scene_takes(scene_id: str):
    """Every take logged for this scene, as the Script Supervisor saw it."""
    rows = client().query(
        f"""
        SELECT a.take_id, a.setup_id, a.shot_size, a.movement, a.subjects,
               a.screen_direction, a.focus_score, a.exposure_score,
               a.continuity_flags, t.duration_s, t.circled
        FROM {DB}.take_analysis AS a
        LEFT JOIN {DB}.takes AS t USING (take_id)
        WHERE a.scene_id = %(s)s
        ORDER BY a.setup_id, a.take_id
        """,
        parameters={"s": scene_id},
    ).result_rows
    return [
        {"take_id": r[0], "setup_id": r[1], "shot_size": r[2],
         "shot_size_plain": SHOT_SIZES.get(r[2], r[2]),
         "movement": r[3], "movement_plain": MOVEMENTS.get(r[3], r[3]),
         "subjects": list(r[4]), "screen_direction": r[5],
         "focus_score": round(float(r[6]), 2),
         "exposure_score": round(float(r[7]), 2),
         "faults": list(r[8]), "duration_s": float(r[9] or 0),
         "circled": bool(r[10])}
        for r in rows
    ]


# --- the library ------------------------------------------------------------

@app.get("/api/library/dps")
def dps():
    """Every DP, with how much we have watched them work."""
    rows = client().query(
        f"""
        SELECT dp_id,
               countMerge(n) AS setups,
               round(quantilesTDigestMerge(0.5)(durations)[1] / 60) AS median_min,
               round(quantilesTDigestMerge(0.9)(durations)[1] / 60) AS p90_min
        FROM {DB}.setup_duration_stats
        GROUP BY dp_id ORDER BY setups DESC
        """
    ).result_rows
    return [
        {**person_label(r[0]), "setups": r[1],
         "median_minutes": r[2], "p90_minutes": r[3]}
        for r in rows
    ]


@app.get("/api/library/dps/{dp_id}")
def dp_detail(dp_id: str):
    """One DP, broken down by the conditions they were shooting in."""
    rows = client().query(
        f"""
        SELECT int_ext, day_night, scene_type, extras_bucket,
               countMerge(n) AS setups,
               round(quantilesTDigestMerge(0.5)(durations)[1] / 60) AS median_min,
               round(quantilesTDigestMerge(0.9)(durations)[1] / 60) AS p90_min
        FROM {DB}.setup_duration_stats
        WHERE dp_id = %(dp)s
        GROUP BY int_ext, day_night, scene_type, extras_bucket
        HAVING setups > 0
        ORDER BY setups DESC
        """,
        parameters={"dp": dp_id},
    ).result_rows

    productions = client().query(
        f"""
        SELECT p.title, p.kind, count() AS setups
        FROM {DB}.setups AS s
        INNER JOIN {DB}.productions AS p USING (production_id)
        WHERE s.dp_id = %(dp)s
        GROUP BY p.title, p.kind ORDER BY setups DESC
        """,
        parameters={"dp": dp_id},
    ).result_rows

    return {
        **person_label(dp_id),
        "conditions": [
            {"interior_exterior": r[0], "time_of_day": r[1], "scene_type": r[2],
             "extras_bucket": r[3], "setups": r[4],
             "median_minutes": r[5], "p90_minutes": r[6]}
            for r in rows
        ],
        "productions": [
            {"title": r[0], "kind": r[1], "setups": r[2]} for r in productions
        ],
    }


@app.get("/api/library/productions")
def productions():
    rows = client().query(
        f"""
        SELECT production_id, title, kind, start_date, end_date,
               shoot_days, primary_dp
        FROM {DB}.productions ORDER BY start_date
        """
    ).result_rows
    return [
        {"production_id": r[0], "title": r[1], "kind": r[2],
         "start_date": str(r[3]), "end_date": str(r[4]), "shoot_days": r[5],
         "dp": person_label(r[6])}
        for r in rows
    ]


# --- the outside world ------------------------------------------------------

@app.post("/api/webhooks/parallel")
async def parallel_webhook(request: Request, background: BackgroundTasks):
    """Parallel Monitor pushes here when something changes at the location.

    This is what stops the system being a thing you have to ask. A pulled
    permit or incoming rain arrives on its own and the day is reassessed.
    """
    payload = await request.json()
    data = payload.get("data", {})

    run = bus.latest()
    if run:
        run.publish("scout", "tool_result",
                    f"Change detected: {payload.get('type', 'event')}",
                    {"monitor_id": data.get("monitor_id")})

    client().insert(
        "world_events",
        [[
            os.environ.get("DEMO_LOCATION_ID", "canal_street"),
            datetime.utcnow(),
            "parallel_monitor",
            data.get("kind", "unknown"),
            int(data.get("severity", 3)),
            str(payload.get("summary", ""))[:500],
            str(data.get("citation_url", "")),
            str(data.get("monitor_id", "")),
            json.dumps(payload)[:4000],
        ]],
        column_names=["location_id", "ts", "source", "kind", "severity",
                      "summary", "citation_url", "monitor_id", "payload"],
    )

    background.add_task(gate, DEFAULT_SCENE)
    return {"received": True}


@app.get("/api/world")
def world(location_id: str = "canal_street", limit: int = 20):
    rows = client().query(
        f"""
        SELECT ts, source, kind, severity, summary, citation_url
        FROM {DB}.world_events
        WHERE location_id = %(l)s ORDER BY ts DESC LIMIT %(n)s
        """,
        parameters={"l": location_id, "n": limit},
    ).result_rows
    return [
        {"ts": r[0].isoformat(), "source": r[1], "kind": r[2],
         "severity": r[3], "summary": r[4], "citation_url": r[5]}
        for r in rows
    ]


# --- reference --------------------------------------------------------------

@app.get("/api/glossary")
def glossary():
    return {"terms": GLOSSARY, "shot_sizes": SHOT_SIZES, "movements": MOVEMENTS}


@app.get("/api/crew")
def crew():
    return [{"key": k, **v} for k, v in AGENTS.items()]


@app.get("/api/health")
def health():
    return {"ok": True}
