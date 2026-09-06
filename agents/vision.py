"""Vision Agent, analyse a take with Gemini.

Reads each clip, asks Gemini for structured analysis, and writes the results to
JSON. Keeping analysis and database insert as separate steps means a failed
write never costs a second round of API calls.
    """

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from agents import gemini

from agents.resilience import retry

load_dotenv()

MODEL = os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash")

SHOT_SIZES = ["ELS", "LS", "MLS", "MS", "MCU", "CU", "ECU"]
MOVEMENTS = ["static", "pan", "tilt", "dolly", "handheld", "crane", "steadicam", "zoom"]

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "location_label": {
            "type": "string",
            "description": "Short stable label for where this was shot, e.g. "
                           "'bridge over canal', 'church interior', 'rooftop'. "
                           "Use the same wording for the same place.",
        },
        "scene_summary": {"type": "string"},
        "shot_size": {"type": "string", "enum": SHOT_SIZES},
        "movement": {"type": "string", "enum": MOVEMENTS},
        "subjects": {
            "type": "array",
            "items": {"type": "string"},
            "description": "People visible, described consistently, e.g. "
                           "'woman with dark hair in red jacket'.",
        },
        "subjects_count": {"type": "integer"},
        "screen_direction": {"type": "string", "enum": ["left", "right", "neutral"]},
        "eyeline_target": {"type": "string"},
        "is_dialogue": {"type": "boolean"},
        "focus_score": {"type": "number", "description": "0..1, sharpness of the intended subject"},
        "exposure_score": {"type": "number", "description": "0..1, how well exposed"},
        "technical_faults": {
            "type": "array",
            "items": {"type": "string"},
            "description": "e.g. 'soft focus', 'boom visible', 'flicker', 'motion blur'",
        },
        "vfx_clean_plate": {"type": "boolean"},
        "vfx_chart_visible": {"type": "boolean"},
        "vfx_markers_visible": {"type": "boolean"},
        "has_vfx": {"type": "boolean"},
        "int_ext": {
            "type": "string",
            "enum": ["INT", "EXT"],
            "description": "Interior or exterior. A vehicle, a doorway seen "
                           "from inside and a covered walkway are all INT.",
        },
        "day_night": {
            "type": "string",
            "enum": ["DAY", "NIGHT"],
            "description": "The time of day the scene reads as, not when it "
                           "was filmed. Judge by the light.",
        },
    },
    "required": [
        "location_label", "scene_summary", "shot_size", "movement",
        "subjects", "subjects_count", "screen_direction", "is_dialogue",
        "focus_score", "exposure_score", "technical_faults",
        "int_ext", "day_night",
    ],
}

PROMPT = """You are a script supervisor logging a take on a film set.

Watch this clip and describe it factually. It is one continuous camera setup
off the camera card, so it may open on a clapperboard before the take starts.

Shot size is where the frame cuts the body, not how close it feels:

- ELS  the figure is small, the place is the subject
- LS   head to feet, with room around them
- MLS  cut at the knees
- MS   cut at the waist
- MCU  cut at the chest, head and shoulders and a little below
- CU   the face fills the frame, cut at the collar
- ECU  part of a face, or a detail

Judge it on how much of the person is in frame. A subject standing away from
the camera in a wide room is not a close-up because you can read their face.

Take the size that holds for most of the take. If the camera does not move and
the subject walks out of frame or behind something, the size is still whatever
it was while they were in it — but say in the summary that they leave, because
a take where the subject is absent for half of it does not cover them.

If there is no person, size the object the way you would size a body: a whole
door is a wide, a hand on a doorknob is a close-up.

Be precise about:
- camera movement
- interior or exterior, and whether it reads as day or night
- who is in frame and which way they are looking
- whether the subject is sharp, and whether anything is technically wrong
- whether this looks like a VFX plate rather than a performance take

Whoever holds the clapperboard is crew marking the take, not someone in the
scene. Do not list them as a subject.

For location_label, use a short consistent phrase. Clips shot in the same place
must get the same label, because these labels are used to group takes into
scenes. A tight insert of an object is not evidence of the room around it, so
label it for what it shows.

Do not speculate about story. Report only what is visible.
"""


def analyse_clip(client, clip):
    path = Path(clip["path"])
    data = path.read_bytes()

    response = retry(
        client.models.generate_content,
        model=MODEL,
        contents=[
            types.Part.from_bytes(data=data, mime_type="video/mp4"),
            PROMPT,
        ],
        config=gemini.config(
            temperature=0,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        ),
    )

    result = json.loads(gemini.text_of(response))
    result["clip_name"] = clip["clip_name"]
    result["camera_roll"] = clip["camera_roll"]
    result["duration_s"] = clip["duration_s"]
    result["start_s"] = clip["start_s"]
    result["model_id"] = MODEL
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="../footage/clips/manifest.json")
    ap.add_argument("--out", default="../footage/clips/analysis.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    clips = json.loads(Path(args.manifest).read_text())
    if args.limit:
        clips = clips[: args.limit]

    client = gemini.client()
    results, failures = [], []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(analyse_clip, client, c): c for c in clips}
        for fut in as_completed(futures):
            clip = futures[fut]
            try:
                r = fut.result()
                results.append(r)
                print(f"{r['clip_name']}  {r['shot_size']:4s} {r['movement']:10s} "
                      f"{r['location_label'][:34]:34s} {len(r['subjects'])}p")
            except Exception as exc:
                failures.append({"clip_name": clip["clip_name"], "error": str(exc)})
                print(f"{clip['clip_name']}  FAILED  {exc}")

    results.sort(key=lambda r: r["clip_name"])
    Path(args.out).write_text(json.dumps(results, indent=2))

    print(f"\nanalysed {len(results)}, failed {len(failures)}")
    print(f"-> {args.out}")

    if results:
        locations = {}
        for r in results:
            locations.setdefault(r["location_label"], []).append(r["clip_name"])
        print("\nlocations found:")
        for loc, names in sorted(locations.items(), key=lambda kv: -len(kv[1])):
            print(f"  {len(names):3d}  {loc}")


if __name__ == "__main__":
    main()
