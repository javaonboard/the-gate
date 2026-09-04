"""Continuity, will these takes cut together?

Every other check looks at one take alone. This one cannot: a prop has not
moved unless it was somewhere else a moment ago, and a coat is only unbuttoned
if it was buttoned in the wide. The error only exists in the comparison.
                     """

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

from agents import gemini

from agents.resilience import retry
from core.coverage import connect

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")
MODEL = os.environ.get("GEMINI_MODEL_PRO",
                       os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash"))

# Comparing every take against every other is quadratic and mostly pointless.
# What matters is whether the angles cut together, so one frame per camera
# position is enough.
MAX_POSITIONS = 8

KINDS = [
    "screen_direction", "prop_position", "wardrobe",
    "physical_state", "hair_makeup", "light",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "will_cut": {
            "type": "boolean",
            "description": "True if these angles can be cut together as they are.",
        },
        "one_line": {"type": "string"},
        "mismatches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": KINDS},
                    "severity": {"type": "string",
                                 "enum": ["blocking", "warning", "note"]},
                    "what": {
                        "type": "string",
                        "description": "What differs, and how. Name both states.",
                    },
                    "between": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The two shot labels that disagree.",
                    },
                    "confidence": {"type": "number"},
                },
                "required": ["kind", "severity", "what", "between", "confidence"],
            },
        },
    },
    "required": ["will_cut", "one_line", "mismatches"],
}

PROMPT = """These frames are different camera positions on the same scene, shot
over an afternoon. They will be cut together.

Your job is to find anything that stops them cutting — a detail that is one way
in one angle and another way in the next. The audience will not name it, but
they will feel it.

Compare them against each other, looking for:

1. SCREEN DIRECTION. In a conversation, each person should look across the
   frame toward the other — one looking left, one looking right. If both look
   the same way, the camera crossed the line and they will appear not to be
   talking to each other. This is the most expensive error here.

2. PROPS. A glass, a bottle, a letter, a chair, a bag — in a different place,
   or gone, or newly present.

3. WARDROBE. A jacket open in one angle and closed in another. A tie. Sleeves.
   A scarf. Buttons.

4. PHYSICAL STATE. How much is left in a glass. Whether someone is wet, dirty,
   bleeding, out of breath. A cigarette's length.

5. HAIR AND MAKEUP. Parted differently, tied back in one angle and loose in
   another.

6. LIGHT. The sun somewhere else, shadows in another direction, one angle much
   warmer than the rest. Common on a long exterior and unfixable afterwards.

Rules:
- Name both states: "the glass is full in A but half empty in D" — not "glass
  continuity issue".
- Say which two shots disagree, using the labels given.
- blocking means an editor genuinely could not cut these together.
- Different framings of the same moment are not a mismatch. Neither is the
  camera being closer. Judge the world in front of the lens, not the lens.
- If they cut together, say so and return an empty list. Most do."""


def grab_frame(video: Path, at: float) -> bytes | None:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}",
         "-i", str(video), "-frames:v", "1", "-vf", "scale=-2:480",
         "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True,
    )
    return out.stdout or None


def compare_scene(client: genai.Client, ch, scene_id: str, clips: Path
                  ) -> dict[str, Any] | None:
    """One representative frame per camera position, compared as a set."""
    rows = ch.query(
        f"""
        SELECT t.setup_id, argMin(t.take_id, t.take_no) AS take_id,
               any(a.shot_size) AS shot_size
        FROM {DB}.takes AS t
        LEFT JOIN {DB}.take_analysis AS a ON a.take_id = t.take_id
        WHERE t.scene_id = %(s)s AND t.status = 'complete'
        GROUP BY t.setup_id
        ORDER BY t.setup_id
        LIMIT {MAX_POSITIONS}
        """,
        parameters={"s": scene_id},
    ).result_rows

    if len(rows) < 2:
        return None

    parts: list[Any] = []
    labels: list[str] = []
    for setup_id, take_id, shot_size in rows:
        video = clips / f"{take_id}.mp4"
        if not video.exists():
            continue
        frame = grab_frame(video, 1.0)
        if not frame:
            continue
        label = f"{setup_id.split('_')[-1]} ({shot_size or 'unknown'})"
        labels.append(label)
        parts.append(types.Part.from_text(text=f"Shot {label}:"))
        parts.append(types.Part.from_bytes(data=frame, mime_type="image/png"))

    if len(labels) < 2:
        return None

    parts.append(types.Part.from_text(text=PROMPT))

    response = retry(
        client.models.generate_content,
        model=MODEL,
        contents=parts,
        config=gemini.config(
            temperature=0,
            response_mime_type="application/json",
            response_schema=SCHEMA,
        ),
    )
    result = json.loads(gemini.text_of(response))
    result["compared"] = labels
    return result


def store(ch, production_id: str, scene_id: str, result: dict[str, Any]) -> None:
    rows = []
    for m in result.get("mismatches", []):
        between = " and ".join(m.get("between", [])[:2]) or "these angles"
        rows.append([
            production_id, scene_id, "", "",
            m.get("kind", "continuity"), m.get("severity", "warning"),
            m.get("what", "")[:400], f"between {between}"[:200],
            -1.0, float(m.get("confidence", 0.5)),
            MODEL, datetime.now(),
        ])
    if rows:
        ch.insert(
            "take_problems", rows,
            column_names=["production_id", "scene_id", "setup_id", "take_id",
                          "category", "severity", "what", "where_in_frame",
                          "at_seconds", "confidence", "model_id", "checked_at"],
        )


INSTRUCTION = """You are the script supervisor. You hold the whole scene in your
head while it is shot out of order over an afternoon, and you are the only
person who will notice that the glass was full in the wide and half empty in
the close.

Report what will not cut, name both states, and say which angles disagree.
Screen direction first — it is the one that cannot be worked around."""


def build_agent(callbacks: dict | None = None):
    from google.adk.agents import Agent

    return Agent(
        model=MODEL,
        name="continuity",
        description="Checks whether the angles of a scene will cut together.",
        instruction=INSTRUCTION,
        tools=[],
        **(callbacks or {}),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="prod_now_sc001")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--clips", default="../footage/clips")
    args = ap.parse_args()

    ch = connect()
    gclient = gemini.client()
    clips = Path(args.clips)

    scenes = (
        [r[0] for r in ch.query(
            f"SELECT scene_id FROM {DB}.scenes WHERE production_id = 'prod_now' "
            f"ORDER BY scene_id").result_rows]
        if args.all else [args.scene]
    )

    for scene_id in scenes:
        production_id = ch.query(
            f"SELECT production_id FROM {DB}.scenes WHERE scene_id = %(s)s",
            parameters={"s": scene_id},
        ).result_rows
        production_id = production_id[0][0] if production_id else "prod_now"

        try:
            result = compare_scene(gclient, ch, scene_id, clips)
        except Exception as exc:
            print(f"{scene_id}  failed: {type(exc).__name__}")
            continue

        if result is None:
            print(f"{scene_id}  only one angle, nothing to compare")
            continue

        store(ch, production_id, scene_id, result)
        mark = "cuts" if result["will_cut"] else "WILL NOT CUT"
        print(f"\n{scene_id}  {mark}  ({len(result['compared'])} angles)")
        print(f"  {result['one_line'][:100]}")
        for m in result.get("mismatches", []):
            print(f"    {m['severity']:8s} {m['kind']:16s} {m['what'][:64]}")
