"""The Editor — where does one shot end and the next begin?

A threshold cannot answer this. Frame-difference detection asks "did the
picture change a lot" and calls that a cut, which works on edited drama and
fails on everything else. Measured on two real files: a threshold tuned for one
found five shots in the other's twenty-two minutes, one of them seventeen
minutes long.

So the same split as everywhere else in this system — ffmpeg proposes, the
model decides. ffmpeg is fast and free and finds the obvious hard cuts. Gemini
actually watches the footage and says where a shot genuinely changes, including
the ones no threshold sees: a dissolve, a whip pan, a cut between two dark
frames, the moment a slate leaves frame and the take begins.

Long files are handled in windows, because a model asked about twenty minutes
at once loses track of the clock.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

from agents.resilience import retry

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

MODEL = os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash")

# How much footage to show the model at once. Long enough to see the rhythm of
# the cutting, short enough that reported timestamps stay accurate.
WINDOW_SECONDS = 300.0

# Small enough to send quickly, large enough to see a cut.
PREVIEW_HEIGHT = 360

# Two boundaries closer than this are the same boundary, seen twice.
SAME_BOUNDARY = 1.2

SHOTS_SCHEMA = {
    "type": "object",
    "properties": {
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "starts_at": {
                        "type": "number",
                        "description": "Seconds from the start of THIS clip where "
                                       "the shot begins. The first is 0.",
                    },
                    "what_changed": {
                        "type": "string",
                        "description": "Why this is a new shot — 'cut to a close-up', "
                                       "'camera repositioned', 'new location', "
                                       "'slate, take begins'.",
                    },
                    "description": {
                        "type": "string",
                        "description": "The shot itself in a few words.",
                    },
                    "is_slate": {
                        "type": "boolean",
                        "description": "True if a clapperboard is visible — raw "
                                       "footage marks its takes this way.",
                    },
                },
                "required": ["starts_at", "what_changed", "description"],
            },
        }
    },
    "required": ["shots"],
}

PROMPT = """Watch this clip and say where each separate shot begins.

A new shot is any point where the footage stops being one continuous piece of
camera work:

- a hard cut to another angle or another place
- a dissolve or fade between two shots
- the camera stopping and restarting — common in raw footage, where several
  takes sit in one file
- a clapperboard appearing, which marks the head of a take
- a whip pan or a cut hidden in movement

Not a new shot:
- the camera panning, tilting, tracking or zooming within one continuous take
- someone walking in or out of frame
- the lighting changing during a take
- a subject moving closer to the lens

Give the time in seconds from the start of THIS clip. The first shot starts
at 0. Be precise about the timings — they are used to cut the file.

If the whole clip is one continuous shot, return a single entry starting at 0."""


def duration_of(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def preview(path: Path, start: float, length: float) -> bytes | None:
    """A small, short piece of the file to show the model."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(path),
         "-vf", f"scale=-2:{PREVIEW_HEIGHT}", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
         "-movflags", "frag_keyframe+empty_moov", "-f", "mp4", "-"],
        capture_output=True,
    )
    return out.stdout or None


def watch_window(client: genai.Client, path: Path, start: float,
                 length: float) -> list[dict[str, Any]]:
    """Ask the model where the shots begin in one window."""
    clip = preview(path, start, length)
    if not clip:
        return []

    response = retry(
        client.models.generate_content,
        model=MODEL,
        contents=[types.Part.from_bytes(data=clip, mime_type="video/mp4"), PROMPT],
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=SHOTS_SCHEMA,
        ),
    )
    shots = json.loads(response.text).get("shots", [])

    # timestamps come back relative to the window
    for shot in shots:
        shot["starts_at"] = float(shot.get("starts_at", 0)) + start
    return shots


def find_shots(client: genai.Client, path: Path, candidates: list[float] | None = None,
               on_step=None) -> list[dict[str, Any]]:
    """Every shot in the file, as the model sees it.

    candidates are ffmpeg's proposals. They are not passed to the model —
    telling it where to look would only make it agree — but they are merged
    afterwards, so an obvious hard cut is never lost because the model was
    looking elsewhere.
    """
    total = duration_of(path)
    if total <= 0:
        return []

    shots: list[dict[str, Any]] = []
    start = 0.0
    while start < total:
        length = min(WINDOW_SECONDS, total - start)
        if on_step:
            on_step(start, total)
        shots.extend(watch_window(client, path, start, length))
        start += length

    # ffmpeg's hard cuts, for anything the model passed over
    for t in candidates or []:
        if all(abs(t - s["starts_at"]) > SAME_BOUNDARY for s in shots):
            shots.append({
                "starts_at": t,
                "what_changed": "hard cut",
                "description": "",
                "is_slate": False,
                "from_detector": True,
            })

    shots.sort(key=lambda s: s["starts_at"])

    merged: list[dict[str, Any]] = []
    for shot in shots:
        if merged and shot["starts_at"] - merged[-1]["starts_at"] < SAME_BOUNDARY:
            continue
        merged.append(shot)

    if merged and merged[0]["starts_at"] > 0.5:
        merged.insert(0, {"starts_at": 0.0, "what_changed": "start of file",
                          "description": "", "is_slate": False})

    for i, shot in enumerate(merged):
        shot["ends_at"] = (merged[i + 1]["starts_at"] if i + 1 < len(merged)
                           else total)
        shot["seconds"] = round(shot["ends_at"] - shot["starts_at"], 2)

    return [s for s in merged if s["seconds"] >= 1.0]


INSTRUCTION = """You break footage into shots. Given a file, you say where each
separate piece of camera work begins and ends.

You exist because measuring how much the picture changed between frames does
not answer the question. It misses dissolves, misses cuts between two dark
frames, and calls a fast pan a cut. Watch it instead."""


def build_agent(callbacks: dict | None = None):
    from google.adk.agents import Agent

    return Agent(
        model=MODEL,
        name="editor",
        description="Decides where one shot ends and the next begins.",
        instruction=INSTRUCTION,
        tools=[],
        **(callbacks or {}),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--compare", action="store_true",
                    help="also run the detector, and show the difference")
    args = ap.parse_args()

    src = Path(args.input)
    client = genai.Client()

    candidates: list[float] = []
    if args.compare:
        from data.split_takes import find_shots as detector_shots
        marks = detector_shots(src, duration_of(src))
        candidates = marks[1:-1]
        print(f"detector alone: {max(0, len(marks) - 1)} shots")

    shots = find_shots(
        client, src, candidates,
        on_step=lambda at, total: print(
            f"  watching {at / 60:.0f}–{min(at + WINDOW_SECONDS, total) / 60:.0f} min"),
    )

    print(f"\n{len(shots)} shots\n")
    for i, s in enumerate(shots, 1):
        mark = "detector" if s.get("from_detector") else "watched"
        slate = " [slate]" if s.get("is_slate") else ""
        print(f"  {i:3d}  {s['starts_at']:7.1f}s  {s['seconds']:6.1f}s  "
              f"{mark:8s}{slate}  {s['what_changed'][:34]:34s} {s['description'][:38]}")
