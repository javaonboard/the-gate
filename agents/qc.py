"""QC — what would stop this take being used.

In 2019 a scene of Game of Thrones went out with a takeaway coffee cup sitting
on the table. Nobody on the day saw it, nobody in post saw it, and by the time
the internet saw it the episode had aired. It cost the production nothing to
fix on the floor and could not be fixed at all afterwards.

That is the job here: look at each take for the things that make footage
unusable, and say so while the camera is still set up.

Three levels, because they lead to different decisions:

  blocking   the take cannot be used — a crew member in shot, a modern object
             in a period scene, the subject out of focus. Another take is needed
             and the gate does not count this one as coverage.

  warning    usable but flawed — a boom shadow, focus drifting at the end, a
             continuity detail that may not match. Worth one more take if there
             is time.

  note       an observation an editor might want. Nothing to do on the day.

Blocking problems feed straight into the coverage matrix: a take with one does
not satisfy a requirement, so a scene can be short on coverage even with the
footage apparently in the can.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

from core.coverage import connect

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")
MODEL = os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash")

# What we look for. Grouped the way a real set divides responsibility: camera
# and sound own the technical faults, production design owns whether the world
# is right, and the script supervisor owns whether it will cut together.
CATEGORIES = {
    # technical — camera and sound
    "crew_or_equipment": "someone or something from the crew is in shot",
    "boom_shadow": "a microphone shadow on a wall or a face",
    "reflection": "the camera or crew reflected in glass or a mirror",
    "focus": "the subject is not sharp",
    "exposure": "too bright or too dark to use",
    "artefact": "flicker, rolling shutter, banding, a dead pixel",
    "framing": "badly composed, cut off, or obstructed",

    # the world — production design
    "anachronism": "something that did not exist in this period",
    "wrong_place": "something that does not belong in this setting",
    "modern_branding": "present-day logos, packaging or signage",

    # will it cut — script supervisor
    "continuity": "a detail that will not match the other takes",
    "screen_direction": "facing the wrong way to cut with the others",
    "performance": "a fluffed line, a look to camera, a broken moment",
}

SEVERITIES = ["blocking", "warning", "note"]

QC_SCHEMA = {
    "type": "object",
    "properties": {
        "usable": {
            "type": "boolean",
            "description": "False if anything makes this take unusable as it is.",
        },
        "one_line": {
            "type": "string",
            "description": "What you would say to the 1st AD, in one sentence.",
        },
        "problems": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "severity": {"type": "string", "enum": SEVERITIES},
                    "what": {
                        "type": "string",
                        "description": "Plainly what is wrong, as a person would say it.",
                    },
                    "where": {
                        "type": "string",
                        "description": "Where in frame, e.g. 'on the table, left of "
                                       "frame' — or empty if it applies throughout.",
                    },
                    "at_seconds": {
                        "type": "number",
                        "description": "When it is visible. -1 if throughout.",
                    },
                    "confidence": {"type": "number"},
                },
                "required": ["category", "severity", "what", "confidence"],
            },
        },
    },
    "required": ["usable", "one_line", "problems"],
}


def build_prompt(period: str, setting: str, notes: str = "") -> str:
    extra = f"Also: {notes}" if notes else ""
    return f"""You are checking a take before the camera moves on. Find anything
that would stop an editor using it.

THE WORLD THIS IS SET IN
Period:  {period or "present day"}
Setting: {setting or "a contemporary setting"}
{extra}

Anything visible that could not exist in that world is a problem, however small
and however far into the background. This is the most valuable thing you can
find: it costs nothing to fix while the camera is still up, and cannot be fixed
once the set is struck.

Work through three passes.

1. IS ANYTHING FROM THE CREW VISIBLE?
   A person, a light stand, a boom, a cable, sandbags, tape marks on the floor,
   a shadow cast by a microphone, the camera or an operator reflected in glass,
   a mirror, a window, a car door, someone's glasses.

2. DOES EVERY OBJECT BELONG IN THIS WORLD?
   Go across the frame object by object, including the background and the very
   edges. A disposable coffee cup, a plastic bottle, a wristwatch, trainers, a
   zip, a phone, a wheelie bin, a parked car, road markings, an aerial, a
   satellite dish, a modern shopfront, a printed logo, a light switch, a power
   socket. Ask of each one: could this exist in this period and this place?
   Say exactly what it is and exactly where, so someone can walk over and move
   it.

3. IS IT TECHNICALLY USABLE?
   Is the intended subject genuinely sharp. Is the exposure recoverable. Any
   flicker, banding or rolling shutter. Is anyone cut off badly or obstructed.
   Is anyone looking down the lens.

Rules:
- blocking means the take genuinely cannot be used: a crew member in shot, an
  object that could not exist, the subject out of focus.
- warning means usable, but worth another take if there is time.
- Do not invent problems. Most takes are clean, and a clean take must come back
  with an empty list and usable = true. A false alarm costs a take nobody needed.
- Judge only what you can actually see.

Be specific. "A white paper cup on the table, left of the actress" is useful.
"Possible continuity issue" is worthless."""


def world_of(ch, production_id: str) -> tuple[str, str, str]:
    """The period and setting this production is meant to be in.

    Without it there is no such thing as an anachronism — a coffee cup is only
    wrong because the scene is medieval.
    """
    rows = ch.query(
        f"SELECT period, setting, notes FROM {DB}.production_world FINAL "
        f"WHERE production_id = %(p)s",
        parameters={"p": production_id},
    ).result_rows
    return tuple(rows[0]) if rows else ("", "", "")


def check_take(client: genai.Client, path: Path, period: str = "",
               setting: str = "", notes: str = "") -> dict[str, Any]:
    """Watch one take and report what would stop it being used."""
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part.from_bytes(data=path.read_bytes(), mime_type="video/mp4"),
            build_prompt(period, setting, notes),
        ],
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=QC_SCHEMA,
        ),
    )
    result = json.loads(response.text)
    result["model_id"] = MODEL
    return result


def store(ch, production_id: str, scene_id: str, setup_id: str, take_id: str,
          result: dict[str, Any]) -> None:
    problems = result.get("problems", [])
    if not problems:
        return
    ch.insert(
        "take_problems",
        [[
            production_id, scene_id, setup_id, take_id,
            p.get("category", "artefact"), p.get("severity", "note"),
            p.get("what", "")[:400], p.get("where", "")[:200],
            float(p.get("at_seconds", -1)), float(p.get("confidence", 0.5)),
            result.get("model_id", MODEL), datetime.now(),
        ] for p in problems],
        column_names=["production_id", "scene_id", "setup_id", "take_id",
                      "category", "severity", "what", "where_in_frame",
                      "at_seconds", "confidence", "model_id", "checked_at"],
    )


def blocking_takes(ch, scene_id: str) -> set[str]:
    """Takes the gate must not count as coverage."""
    return {
        r[0] for r in ch.query(
            f"""
            SELECT DISTINCT take_id FROM {DB}.take_problems
            WHERE scene_id = %(s)s AND severity = 'blocking'
            """,
            parameters={"s": scene_id},
        ).result_rows
    }


# --- the agent --------------------------------------------------------------

INSTRUCTION = """You check footage before the crew moves on.

You are looking for the thing nobody noticed. A coffee cup on a medieval table,
a crew member reflected in a window, a boom dipping into frame. These cost
nothing to fix while the camera is still up and cannot be fixed afterwards.

When you report:
- Lead with whether the take is usable.
- Say exactly what and exactly where, so someone can go and look.
- Separate what blocks the take from what merely bothers you.
- If it is clean, say so in a few words. Do not manufacture concerns.

You are the last person to see this before it goes."""


def build_agent(callbacks: dict | None = None):
    from google.adk.agents import Agent

    return Agent(
        model=MODEL,
        name="qc",
        description="Finds what would stop a take being used.",
        instruction=INSTRUCTION,
        tools=[],
        **(callbacks or {}),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="prod_now_sc001")
    ap.add_argument("--clips", default="../footage/clips")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--period", default="")
    ap.add_argument("--setting", default="")
    args = ap.parse_args()

    ch = connect()
    gclient = genai.Client()

    rows = ch.query(
        f"""
        SELECT t.production_id, t.scene_id, t.setup_id, t.take_id
        FROM {DB}.takes AS t WHERE t.scene_id = %(s)s
        ORDER BY t.setup_id, t.take_no
        """,
        parameters={"s": args.scene},
    ).result_rows
    if args.limit:
        rows = rows[: args.limit]

    clips = Path(args.clips)
    clean = flagged = 0

    for production_id, scene_id, setup_id, take_id in rows:
        video = clips / f"{take_id}.mp4"
        if not video.exists():
            continue
        try:
            result = check_take(gclient, video, args.period, args.setting)
        except Exception as exc:
            print(f"{take_id:16s} skipped — {type(exc).__name__}")
            continue

        store(ch, production_id, scene_id, setup_id, take_id, result)
        problems = result.get("problems", [])
        worst = ("blocking" if any(p["severity"] == "blocking" for p in problems)
                 else "warning" if any(p["severity"] == "warning" for p in problems)
                 else "clean")
        if worst == "clean":
            clean += 1
        else:
            flagged += 1
        print(f"{take_id:16s} {worst:9s} {result['one_line'][:70]}", flush=True)
        for p in problems:
            if p["severity"] != "note":
                print(f"                   · {p['severity']:8s} {p['what'][:62]}")

    print(f"\n{clean} clean, {flagged} flagged")
