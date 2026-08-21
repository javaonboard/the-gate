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

# What we look for, in the language a set uses.
CATEGORIES = {
    "crew_or_equipment": "someone or something from the crew is in shot",
    "anachronism": "an object that does not belong in this world or period",
    "focus": "the subject is not sharp",
    "exposure": "too bright or too dark to use",
    "framing": "badly composed, cut off, or obstructed",
    "continuity": "a detail that will not match the other takes",
    "performance": "a fluffed line, a look to camera, a broken moment",
    "artefact": "flicker, rolling shutter, compression, a dead pixel",
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


def build_prompt(period: str, setting: str) -> str:
    return f"""You are checking a take before the camera moves on. Look for
anything that would stop an editor using it.

The scene is set in: {setting or "a contemporary setting"}.
Period: {period or "present day"}.

Look for, in this order of importance:

1. Anyone or anything belonging to the crew — a person, a light stand, a boom,
   a cable, tape on the floor, a reflection of the camera in glass or a mirror.

2. Objects that do not belong in this world. A modern cup, a plastic bottle, a
   wristwatch in a period scene, a car in the background of a medieval street,
   a mobile phone where there should not be one. This is the most valuable thing
   you can find and the easiest to miss.

3. Whether the intended subject is genuinely sharp, and whether the exposure is
   usable.

4. Framing — anyone cut off badly, anything blocking the subject, headroom
   plainly wrong.

5. Anything an editor would find awkward: a look to camera, a visible mistake,
   a flicker or artefact.

Rules:
- Mark something blocking only if the take genuinely could not be used. A crew
  member in shot is blocking. A slightly soft frame is a warning.
- Say where it is, so someone can look at it.
- Do not invent problems. A clean take should come back with an empty list and
  usable = true. Most takes are clean.
- Judge what you can see, not what you imagine.

Be specific. "A white paper cup on the table, right of frame" is useful.
"Possible continuity issue" is not."""


def check_take(client: genai.Client, path: Path, period: str = "",
               setting: str = "") -> dict[str, Any]:
    """Watch one take and report what would stop it being used."""
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part.from_bytes(data=path.read_bytes(), mime_type="video/mp4"),
            build_prompt(period, setting),
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
