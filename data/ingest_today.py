"""Ingest analysed clips as today's shoot day.

Takes the Vision Agent's output for one location, turns it into a production,
a shoot day, a scene, its editorial requirements, setups and takes, and loads it
into ClickHouse alongside the studio's historical library.
    """

import argparse
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import clickhouse_connect
from dotenv import load_dotenv

load_dotenv()

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")

PRODUCTION_ID = "prod_now"
PRODUCTION_TITLE = "Tears of Steel"
DP_ID = "dp_lind"          # the slow one — makes the day genuinely tight
UNION_LOCAL = "IATSE_600"

WIDE = {"ELS", "LS", "MLS"}
TIGHT = {"MCU", "CU", "ECU"}


def load_takes(analysis_path, location):
    takes = json.loads(Path(analysis_path).read_text())
    return sorted(
        [t for t in takes if t.get("canonical_location") == location],
        key=lambda t: t["clip_name"],
    )


def setup_key(t):
    """Clips sharing framing and direction came from one camera position."""
    band = "wide" if t["shot_size"] in WIDE else "tight" if t["shot_size"] in TIGHT else "mid"
    return band, t["screen_direction"], min(t["subjects_count"], 3)


def build_requirements(scene_id, cast_size):
    """Standard coverage an editor needs to cut a scene."""
    reqs = [("master", "-", 1, 45000, 0)]
    for i in range(cast_size):
        reqs.append(("single", f"actor_{i + 1}", 1, 30000, 0))
        if cast_size > 1:
            reqs.append(("ots", f"actor_{i + 1}", 2, 18000, 0))
    reqs.append(("insert", "prop", 2, 6000, 0))
    return [
        [scene_id, f"{scene_id}_r{i:02d}", shot, subj, pri, cost, plate]
        for i, (shot, subj, pri, cost, plate) in enumerate(reqs)
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", default="../footage/clips/analysis.json")
    ap.add_argument("--location", required=True)
    ap.add_argument("--hold", default="", help="comma-separated clip names to withhold")
    ap.add_argument("--day", default=str(date.today()))
    ap.add_argument("--list", action="store_true", help="show the plan, write nothing")
    args = ap.parse_args()

    held = {c.strip() for c in args.hold.split(",") if c.strip()}
    takes = load_takes(args.analysis, args.location)
    if not takes:
        raise SystemExit(f"no clips at location {args.location!r}")

    kept = [t for t in takes if t["clip_name"] not in held]
    shoot_day = date.fromisoformat(args.day)
    scene_id = f"{PRODUCTION_ID}_sc001"
    cast_size = max(t["subjects_count"] for t in takes) or 1

    groups = defaultdict(list)
    for t in kept:
        groups[setup_key(t)].append(t)

    print(f"location   {args.location}")
    print(f"clips      {len(takes)}  kept {len(kept)}  held {len(held)}")
    print(f"cast size  {cast_size}")
    print(f"setups     {len(groups)}\n")
    for key, items in sorted(groups.items()):
        band, direction, people = key
        names = ", ".join(t["clip_name"].split("_")[-1] for t in items)
        print(f"  {band:5s} {direction:7s} {people}p  {len(items):2d} takes   {names}")
    if held:
        print(f"\n  withheld: {', '.join(sorted(held))}")

    if args.list:
        return

    client = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        port=int(os.environ.get("CLICKHOUSE_PORT", 8443)),
        username=os.environ["CLICKHOUSE_USER"],
        password=os.environ["CLICKHOUSE_PASSWORD"],
        secure=True,
        database=DB,
    )

    for table in ["productions", "scenes", "scene_requirements", "shoot_days",
                  "setups", "takes", "take_analysis", "crew_hours"]:
        client.command(
            f"ALTER TABLE {DB}.{table} DELETE WHERE "
            + (f"production_id = '{PRODUCTION_ID}'" if table != "scene_requirements"
               else f"scene_id = '{scene_id}'")
        )

    call = datetime.combine(shoot_day, datetime.min.time()) + timedelta(hours=7)
    summary = takes[0]["scene_summary"][:180]

    client.insert(
        "productions",
        [[PRODUCTION_ID, PRODUCTION_TITLE, "feature", shoot_day, shoot_day, 1, DP_ID]],
        column_names=["production_id", "title", "kind", "start_date", "end_date",
                      "shoot_days", "primary_dp"],
    )
    client.insert(
        "scenes",
        [[PRODUCTION_ID, scene_id, 1.0, 12, "EXT", "DAY", "dialogue",
          args.location.replace(" ", "_"),
          [f"ACTOR_{i + 1}" for i in range(cast_size)], summary]],
        column_names=["production_id", "scene_id", "script_page", "page_eighths",
                      "int_ext", "day_night", "scene_type", "location_id",
                      "characters", "synopsis"],
    )
    client.insert(
        "scene_requirements",
        build_requirements(scene_id, cast_size),
        column_names=["scene_id", "req_id", "shot_type", "subject", "priority",
                      "recover_cost_usd", "is_vfx_plate"],
    )
    client.insert(
        "shoot_days",
        [[PRODUCTION_ID, shoot_day, "main", args.location.replace(" ", "_"),
          call, None, call - timedelta(minutes=45), call + timedelta(hours=12),
          [scene_id]]],
        column_names=["production_id", "shoot_day", "unit", "location_id",
                      "call_time", "wrap_time", "sunrise", "sunset", "planned_scenes"],
    )

    setup_rows, take_rows, analysis_rows = [], [], []
    clock = call + timedelta(minutes=45)

    for n, (key, items) in enumerate(sorted(groups.items())):
        band, direction, people = key
        setup_id = f"{scene_id}_{chr(65 + n)}"
        actual = int(sum(t["duration_s"] for t in items) * 12) + 900
        start_ts, end_ts = clock, clock + timedelta(seconds=actual)
        clock = end_ts + timedelta(minutes=8)

        setup_rows.append([
            PRODUCTION_ID, shoot_day, scene_id, setup_id, start_ts, end_ts,
            2400, actual, args.location.replace(" ", "_"), "EXT", "DAY",
            "dialogue", 0, DP_ID, 62,
        ])

        for i, t in enumerate(items, start=1):
            take_id = t["clip_name"]
            faults = t.get("technical_faults", [])
            take_rows.append([
                PRODUCTION_ID, shoot_day, scene_id, setup_id, i, take_id,
                t["camera_roll"], t["clip_name"], "10:00:00:00", "10:00:00:00",
                t["duration_s"], 0.0, 0.0, "none", 800, 24.0, "SONY_F65",
                "complete", 1 if i == len(items) else 0, 0.0,
                f"file://../footage/clips/{take_id}.mp4",
            ])
            analysis_rows.append([
                PRODUCTION_ID, shoot_day, scene_id, setup_id, take_id,
                t["shot_size"], t["movement"], t.get("subjects", []),
                t["screen_direction"], t.get("eyeline_target", ""),
                float(t["focus_score"]), float(t["exposure_score"]), faults,
                int(bool(t.get("vfx_clean_plate"))),
                int(bool(t.get("vfx_chart_visible"))), 0,
                int(bool(t.get("vfx_markers_visible"))), 0,
                t.get("model_id", ""), start_ts,
            ])

    client.insert("setups", setup_rows, column_names=[
        "production_id", "shoot_day", "scene_id", "setup_id", "start_ts", "end_ts",
        "planned_duration_s", "actual_duration_s", "location_id", "int_ext",
        "day_night", "scene_type", "extras_count", "dp_id", "crew_size"])
    client.insert("takes", take_rows, column_names=[
        "production_id", "shoot_day", "scene_id", "setup_id", "take_no", "take_id",
        "camera_roll", "clip_name", "tc_start", "tc_end", "duration_s", "lens_mm",
        "t_stop", "nd", "iso", "fps", "camera_body", "status", "circled",
        "slate_confidence", "proxy_uri"])
    client.insert("take_analysis", analysis_rows, column_names=[
        "production_id", "shoot_day", "scene_id", "setup_id", "take_id", "shot_size",
        "movement", "subjects", "screen_direction", "eyeline_target", "focus_score",
        "exposure_score", "continuity_flags", "vfx_clean_plate", "vfx_chart",
        "vfx_grey_ball", "vfx_markers", "vfx_lens_grid", "model_id", "analysed_at"])

    crew = [[PRODUCTION_ID, shoot_day, f"{d}_{p:02d}", d, call, None,
             [call + timedelta(hours=6)], 0, UNION_LOCAL]
            for d in ["camera", "grip", "electric", "sound", "ad"]
            for p in range(4)]
    client.insert("crew_hours", crew, column_names=[
        "production_id", "shoot_day", "person_id", "department", "call_ts",
        "wrap_ts", "meal_breaks", "is_minor", "union_local"])

    print(f"\ninserted  {len(setup_rows)} setups, {len(take_rows)} takes")
    print("note: take_frames not written — per-second analysis is a later pass")


if __name__ == "__main__":
    main()
