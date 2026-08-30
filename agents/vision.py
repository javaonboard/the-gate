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
    },
    "required": [
        "location_label", "scene_summary", "shot_size", "movement",
        "subjects", "subjects_count", "screen_direction", "is_dialogue",
        "focus_score", "exposure_score", "technical_faults",
    ],
}

PROMPT = """You are a script supervisor logging a take on a film set.

Watch this clip and describe it factually. It is one continuous camera setup
from a finished film.
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
