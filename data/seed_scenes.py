"""Turn the analysed footage into scenes.

A scene is one continuous piece of story in one place, the canal street, the
workshop, the tower balcony. The Vision Agent already worked out where each clip
was shot; this makes each of those places a scene you can shoot into.
    """

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from core.coverage import connect

DB = "the_gate"
PRODUCTION_ID = "prod_now"
PRODUCTION_TITLE = "Tears of Steel"
DP_ID = "dp_lind"

WIDE = {"ELS", "LS", "MLS"}
TIGHT = {"MCU", "CU", "ECU"}

# Which of these places are interiors, so the schedule maths is not nonsense.
INTERIOR_WORDS = ("interior", "room", "cabin", "workshop", "complex", "ship",
                  "lab", "tower interior", "chamber")


def looks_interior(location: str) -> bool:
    return any(word in location.lower() for word in INTERIOR_WORDS)


def band(shot_size: str) -> str:
    if shot_size in WIDE:
        return "wide"
    if shot_size in TIGHT:
        return "tight"
    return "mid"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", default="../footage/clips/analysis.json")
    ap.add_argument("--min-clips", type=int, default=3)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    takes = json.loads(Path(args.analysis).read_text())
    by_place: dict[str, list[dict]] = defaultdict(list)
    for t in takes:
        place = t.get("canonical_location")
        if place:
            by_place[place].append(t)

    places = {p: c for p, c in by_place.items() if len(c) >= args.min_clips}
    ch = connect()

    if args.reset:
        for table in ("scenes", "scene_requirements", "setups", "takes",
                      "take_analysis", "shoot_days", "crew_hours",
                      "take_characters", "characters"):
            key = "scene_id" if table == "scene_requirements" else "production_id"
            value = "" if table == "scene_requirements" else PRODUCTION_ID
            if table == "scene_requirements":
                ch.command(f"ALTER TABLE {DB}.{table} DELETE "
                           f"WHERE startsWith(scene_id, '{PRODUCTION_ID}')")
            else:
                ch.command(f"ALTER TABLE {DB}.{table} DELETE "
                           f"WHERE {key} = '{value}'")
        print("cleared")

    shoot_day = date.today()
    ch.command(f"ALTER TABLE {DB}.productions DELETE "
               f"WHERE production_id = '{PRODUCTION_ID}'")
    ch.insert("productions", [[
        PRODUCTION_ID, PRODUCTION_TITLE, "feature", shoot_day, shoot_day,
        1, DP_ID,
    ]], column_names=["production_id", "title", "kind", "start_date",
                      "end_date", "shoot_days", "primary_dp"])

    call = datetime.combine(shoot_day, datetime.min.time()) + timedelta(hours=7)
    clock = call + timedelta(minutes=45)

    scene_rows, setup_rows, take_rows, analysis_rows = [], [], [], []
    scene_ids = []

    for n, (place, clips) in enumerate(
        sorted(places.items(), key=lambda kv: -len(kv[1])), start=1
    ):
        scene_id = f"{PRODUCTION_ID}_sc{n:03d}"
        scene_ids.append(scene_id)
        interior = looks_interior(place)
        cast_size = max((c.get("subjects_count", 0) for c in clips), default=1) or 1

        scene_rows.append([
            PRODUCTION_ID, scene_id, float(n), 12,
            "INT" if interior else "EXT", "DAY", "dialogue",
            place.replace(" ", "_"),
            [f"ACTOR_{i + 1}" for i in range(cast_size)],
            clips[0].get("scene_summary", "")[:180],
        ])

        # camera positions, grouped by framing and which way people face
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for c in clips:
            groups[(band(c["shot_size"]), c.get("screen_direction", "neutral"),
                    min(c.get("subjects_count", 0), 3))].append(c)

        for i, (_, members) in enumerate(sorted(groups.items())):
            setup_id = f"{scene_id}_{chr(65 + i)}"
            actual = int(sum(m["duration_s"] for m in members) * 12) + 900
            start_ts, end_ts = clock, clock + timedelta(seconds=actual)
            clock = end_ts + timedelta(minutes=6)

            setup_rows.append([
                PRODUCTION_ID, shoot_day, scene_id, setup_id, start_ts, end_ts,
                2400, actual, place.replace(" ", "_"),
                "INT" if interior else "EXT", "DAY", "dialogue", 0, DP_ID, 62,
            ])

            for k, m in enumerate(members, start=1):
                take_id = m["clip_name"]
                take_rows.append([
                    PRODUCTION_ID, shoot_day, scene_id, setup_id, k, take_id,
                    m["camera_roll"], take_id, "10:00:00:00", "10:00:00:00",
                    m["duration_s"], 0.0, 0.0, "none", 800, 24.0, "SONY_F65",
                    "complete", 1 if k == len(members) else 0, 0.0,
                    f"file://../footage/clips/{take_id}.mp4",
                ])
                analysis_rows.append([
                    PRODUCTION_ID, shoot_day, scene_id, setup_id, take_id,
                    m["shot_size"], m["movement"], m.get("subjects", []),
                    m.get("screen_direction", "neutral"),
                    m.get("eyeline_target", ""), float(m["focus_score"]),
                    float(m["exposure_score"]), m.get("technical_faults", []),
                    int(bool(m.get("vfx_clean_plate"))),
                    int(bool(m.get("vfx_chart_visible"))), 0,
                    int(bool(m.get("vfx_markers_visible"))), 0,
                    m.get("model_id", ""), start_ts,
                ])

        print(f"{scene_id}  {place:26s} {len(clips):3d} clips"
              f"  {len(groups)} positions")

    ch.insert("scenes", scene_rows, column_names=[
        "production_id", "scene_id", "script_page", "page_eighths", "int_ext",
        "day_night", "scene_type", "location_id", "characters", "synopsis"])
    ch.insert("setups", setup_rows, column_names=[
        "production_id", "shoot_day", "scene_id", "setup_id", "start_ts",
        "end_ts", "planned_duration_s", "actual_duration_s", "location_id",
        "int_ext", "day_night", "scene_type", "extras_count", "dp_id",
        "crew_size"])
    ch.insert("takes", take_rows, column_names=[
        "production_id", "shoot_day", "scene_id", "setup_id", "take_no",
        "take_id", "camera_roll", "clip_name", "tc_start", "tc_end",
        "duration_s", "lens_mm", "t_stop", "nd", "iso", "fps", "camera_body",
        "status", "circled", "slate_confidence", "proxy_uri"])
    ch.insert("take_analysis", analysis_rows, column_names=[
        "production_id", "shoot_day", "scene_id", "setup_id", "take_id",
        "shot_size", "movement", "subjects", "screen_direction",
        "eyeline_target", "focus_score", "exposure_score", "continuity_flags",
        "vfx_clean_plate", "vfx_chart", "vfx_grey_ball", "vfx_markers",
        "vfx_lens_grid", "model_id", "analysed_at"])

    ch.insert("shoot_days", [[
        PRODUCTION_ID, shoot_day, "main", "canal_street", call, None,
        call - timedelta(minutes=45), call + timedelta(hours=12), scene_ids,
    ]], column_names=["production_id", "shoot_day", "unit", "location_id",
                      "call_time", "wrap_time", "sunrise", "sunset",
                      "planned_scenes"])

    crew = [[PRODUCTION_ID, shoot_day, f"{d}_{p:02d}", d, call, None,
             [call + timedelta(hours=6)], 0, "IATSE_600"]
            for d in ["camera", "grip", "electric", "sound", "ad"]
            for p in range(4)]
    ch.insert("crew_hours", crew, column_names=[
        "production_id", "shoot_day", "person_id", "department", "call_ts",
        "wrap_ts", "meal_breaks", "is_minor", "union_local"])

    print(f"\n{len(scene_rows)} scenes, {len(setup_rows)} positions, "
          f"{len(take_rows)} takes")
    print("now run:  python -m agents.casting --all")


if __name__ == "__main__":
    main()
