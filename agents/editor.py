"""The Editor, where does one shot end and the next begin?

A threshold cannot answer this. Frame-difference detection asks "did the
picture change a lot" and calls that a cut, which works on edited drama and
fails on everything else.
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

from agents import gemini

from agents.resilience import retry

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

MODEL = os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash")

# How much footage to show the model at once. Long enough to see the rhythm of
# the cutting, short enough that reported timestamps stay accurate.
WINDOW_SECONDS = 300.0

# Small enough to send quickly, large enough to see a cut.
PREVIEW_HEIGHT = 360

# The tail of a file rarely divides evenly into windows, and what is left over
MIN_WINDOW_SECONDS = 2.0

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
- a clapperboard being held up, which marks the head of a take
- a reset: the frame empties, nothing happens for a while, and then the same
  action is performed again from the top. That is the next take, even though
  the camera never stopped rolling. Not every take is slated
- a whip pan that lands somewhere genuinely different, hiding a cut

Listen as well as watch. The sound is often the only thing that separates two
takes of the same action: somebody off camera calling action, cut, back to
one, or going again; a clapperboard being struck; the same line or the same
scream performed a second time. Two runs at a chase down one alley look alike
and do not sound alike.

Not all of that voice is a boundary. On a small shoot the director talks the
whole way through — keep going, slower, again, hold there — and the actors
work straight past it. Live direction over a running take does not end it.
What ends it is the performance stopping: the actors come out of it, reset,
and go from the top.

Not a new shot:
- the camera panning, tilting, tracking or zooming within one continuous take
- the clapperboard being clapped and pulled out of frame. The camera is still
  rolling. The board and the take it heads are one shot, and it starts at the
  board — never return a shot that is only the board
- the focus going soft, hunting, or losing the subject entirely and coming
  back. An operator pulling focus through a struggle is one take badly shot,
  not two takes
- the camera being thrown about because the action is violent. Handheld work
  on a fight or a chase swings hard and lands back on the same thing
- someone walking in or out of frame
- the lighting changing during a take
- a subject moving closer to the lens

When the picture falls apart for a second and comes back on the same action in
the same place, the camera never stopped and nobody went again. That is one
take. Ask what the people in it were doing, not what the lens was doing.

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
    """A small, short piece of the file to show the model.

    With its sound. This used to strip the audio to keep the upload small,
    which threw away the one cue a set actually uses to mark a take: somebody
    calling action and cut, the clap, the same line delivered again. Two runs
    at a chase down the same alley look alike and do not sound alike. Mono at
    48k adds about a tenth to the file and settles boundaries that the picture
    on its own cannot.
    """
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(path),
         "-vf", f"scale=-2:{PREVIEW_HEIGHT}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
         "-c:a", "aac", "-b:a", "48k", "-ac", "1",
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
        config=gemini.config(
            temperature=0,
            response_mime_type="application/json",
            response_schema=SHOTS_SCHEMA,
        ),
    )
    shots = json.loads(gemini.text_of(response)).get("shots", [])

    # timestamps come back relative to the window
    for shot in shots:
        shot["starts_at"] = float(shot.get("starts_at", 0)) + start
    return shots


def find_shots(client: genai.Client, path: Path, candidates: list[float] | None = None,
               on_step=None) -> list[dict[str, Any]]:
    """Every shot in the file, as the model sees it.

    candidates are ffmpeg's proposals. They are not passed to the model, since
    telling it where to look would only make it agree, but they are merged
    afterwards, so an obvious hard cut is never lost because the model was
    looking elsewhere.
    """
    total = duration_of(path)
    if total <= 0:
        return []

    shots: list[dict[str, Any]] = []
    start = 0.0
    while total - start >= MIN_WINDOW_SECONDS:
        length = min(WINDOW_SECONDS, total - start)
        if on_step:
            on_step(start, total)
        shots.extend(watch_window(client, path, start, length))
        start += length

    # A file shorter than one window is still footage. Reporting "no shots
    # found" because the loop never ran would be a wrong answer given quietly.
    if not shots and total > 0:
        if on_step:
            on_step(0.0, total)
        shots.extend(watch_window(client, path, 0.0, total))

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
    client = gemini.client()

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
