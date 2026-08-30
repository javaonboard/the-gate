"""Generate a studio's production library and load it into ClickHouse.

A studio does not shoot one continuous thousand-day film. It shoots a slate:
features of 30-60 days and series seasons of 60-80, spread over several years,
drawing on the same recurring pool of DPs and crew.
"""

import argparse
import os
import random
from datetime import date, datetime, timedelta

import clickhouse_connect
from dotenv import load_dotenv

load_dotenv()

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")

# --- the studio's recurring people ----------------------------------------

DPS = {
    "dp_reyes": 0.92,   # fast
    "dp_okafor": 1.00,  # baseline
    "dp_lind": 1.18,    # meticulous, slower
    "dp_navarro": 1.05,
    "dp_whitfield": 0.96,
}

DEPARTMENTS = ["camera", "grip", "electric", "sound", "art", "wardrobe", "hmu", "ad"]
CREW_POOL_PER_DEPT = 14   # the studio's bench; each production draws from it

TITLES = [
    "Ironwood", "The Long Falling", "Salt Harbour", "Nightjar", "Cold Open",
    "The Undertow", "Fever Season", "Ashgrove", "Blue Hour", "The Quiet Part",
    "Tidewater", "Signal Fire", "The Understudy", "Northlight", "Rough Cut",
    "The Second Unit", "Blackwater Bend", "Last Looks", "The Gaffer",
    "Magic Hour", "Dead Air", "The Reshoot", "Wrap Party", "Sound Speed",
]

LOCATIONS = [
    ("loc_oude_kerk", "INT"),
    ("loc_rooftop", "EXT"),
    ("loc_alley", "EXT"),
    ("loc_lab", "INT"),
    ("loc_apartment", "INT"),
    ("loc_bridge", "EXT"),
    ("loc_warehouse", "INT"),
    ("loc_forest_road", "EXT"),
]

CHARACTERS = ["THOM", "CELIA", "BARLEY", "MARCUS", "SARAH", "IVO", "REN", "DALIA"]

SCENE_TYPES = ["dialogue", "action", "stunt", "vfx", "montage"]
SCENE_TYPE_WEIGHTS = [0.55, 0.20, 0.08, 0.12, 0.05]

# Industry reference figures behind the distributions below.
#   Features shoot 3-5 script pages/day; TV drama 7-8; low-budget 4-6.
#   A dialogue two-hander in one room runs 7-8 setups.

BASE_MINUTES = {
    "dialogue": 32,
    "action": 55,
    "stunt": 85,
    "vfx": 70,
    "montage": 25,
}

SETUPS_PER_SCENE = {
    "dialogue": (5, 8),
    "action": (8, 14),
    "stunt": (6, 12),
    "vfx": (4, 9),
    "montage": (3, 6),
}

MEAN_TAKES = {
    "dialogue": 3.0,
    "action": 5.5,
    "stunt": 6.5,
    "vfx": 4.0,
    "montage": 2.5,
}

SHOT_SIZES = ["ELS", "LS", "MLS", "MS", "MCU", "CU", "ECU"]
MOVEMENTS = ["static", "pan", "tilt", "dolly", "handheld", "crane", "steadicam"]
MOVEMENT_WEIGHTS = [0.45, 0.12, 0.06, 0.15, 0.14, 0.03, 0.05]

LENSES = [18.0, 25.0, 32.0, 40.0, 50.0, 75.0, 100.0, 135.0]
ND_FILTERS = ["none", "0.3", "0.6", "0.9", "1.2"]

CONTINUITY_FLAGS = [
    "prop_moved", "wardrobe_mismatch", "hair_continuity",
    "eyeline_mismatch", "screen_direction_break", "glass_level_mismatch",
]


def setup_minutes(rng, scene_type, int_ext, day_night, extras, dp_id):
    """Setup duration in minutes, lognormal, conditioned on context."""
    base = BASE_MINUTES[scene_type]
    if int_ext == "EXT":
        base *= 1.25
    if day_night == "NIGHT":
        base *= 1.35
    base *= 1.0 + min(extras, 100) / 120.0
    base *= DPS[dp_id]
    return max(6.0, rng.lognormvariate(0, 0.32) * base)


def build_slate(rng, n_productions, first_start):
    """The studio's slate: overlapping productions across several years."""
    slate = []
    titles = rng.sample(TITLES, min(n_productions, len(TITLES)))
    while len(titles) < n_productions:
        titles.append(f"Untitled {len(titles) + 1}")

    cursor = first_start
    for i in range(n_productions):
        kind = rng.choices(["feature", "series"], [0.6, 0.4])[0]
        days = rng.randint(32, 58) if kind == "feature" else rng.randint(56, 80)
        # productions overlap, a studio runs several at once
        cursor = cursor + timedelta(days=rng.randint(18, 70))
        slate.append({
            "production_id": f"prod_{i + 1:02d}",
            "title": titles[i],
            "kind": kind,
            "start": cursor,
            "days": days,
            "dp": rng.choices(list(DPS), [0.25, 0.25, 0.2, 0.15, 0.15])[0],
        })
    return slate


def build_scenes(rng, production_id, n_scenes):
    scenes, requirements = [], []
    for i in range(1, n_scenes + 1):
        scene_id = f"{production_id}_sc{i:03d}"
        location_id, int_ext = rng.choice(LOCATIONS)
        day_night = rng.choices(["DAY", "NIGHT", "DUSK", "DAWN"], [0.55, 0.32, 0.08, 0.05])[0]
        scene_type = rng.choices(SCENE_TYPES, SCENE_TYPE_WEIGHTS)[0]
        cast = rng.sample(CHARACTERS, rng.randint(1, 3))
        eighths = rng.randint(2, 24)

        scenes.append([
            production_id, scene_id, round(i * 1.4, 1), eighths, int_ext,
            day_night, scene_type, location_id, cast, f"Scene {i}",
        ])

        reqs = [("master", "-", 1, 45000, 0)]
        for c in cast:
            reqs.append(("single", c, 1, 30000, 0))
            if len(cast) > 1:
                reqs.append(("ots", c, 2, 18000, 0))
        if rng.random() < 0.35:
            reqs.append(("insert", "prop", 2, 6000, 0))
        if rng.random() < 0.25:
            reqs.append(("reaction", rng.choice(cast), 3, 5000, 0))
        if scene_type == "vfx":
            reqs.append(("plate", "-", 1, 40000, 1))

        for j, (shot_type, subject, priority, cost, is_plate) in enumerate(reqs):
            requirements.append([
                scene_id, f"{scene_id}_r{j:02d}", shot_type, subject,
                priority, cost, is_plate,
            ])
    return scenes, requirements


def generate(n_productions, first_start, seed):
    rng = random.Random(seed)
    slate = build_slate(rng, n_productions, first_start)

    out = {
        "productions": [], "scenes": [], "scene_requirements": [],
        "shoot_days": [], "setups": [], "takes": [], "take_analysis": [],
        "take_frames": [], "crew_hours": [],
    }

    for prod in slate:
        pid, dp_primary = prod["production_id"], prod["dp"]
        n_scenes = max(8, prod["days"] * 4)
        scenes, reqs = build_scenes(rng, pid, n_scenes)
        out["scenes"] += scenes
        out["scene_requirements"] += reqs

        out["productions"].append([
            pid, prod["title"], prod["kind"], prod["start"],
            prod["start"] + timedelta(days=prod["days"] + rng.randint(4, 20)),
            prod["days"], dp_primary,
        ])

        # this production's crew, drawn from the studio's bench
        crew = {
            dept: rng.sample(range(CREW_POOL_PER_DEPT), rng.randint(2, 6))
            for dept in DEPARTMENTS
        }

        scene_cursor = 0
        for d in range(prod["days"]):
            day = prod["start"] + timedelta(days=d)
            # second unit or a replacement DP occasionally
            dp_id = dp_primary if rng.random() < 0.88 else rng.choice(list(DPS))
            call = datetime.combine(day, datetime.min.time()) + timedelta(hours=7)
            sunrise = call - timedelta(minutes=rng.randint(30, 90))
            sunset = call + timedelta(hours=rng.randint(11, 14))

            day_scenes = scenes[scene_cursor:scene_cursor + rng.randint(2, 4)]
            scene_cursor += len(day_scenes)
            if not day_scenes:
                break

            location_id = day_scenes[0][7]
            out["shoot_days"].append([
                pid, day, "main", location_id, call, None, sunrise, sunset,
                [s[1] for s in day_scenes],
            ])

            for dept, people in crew.items():
                for p in people:
                    out["crew_hours"].append([
                        pid, day, f"{dept}_{p:02d}", dept, call, None,
                        [call + timedelta(hours=6)], 0, "IATSE_600",
                    ])

            clock = call + timedelta(minutes=rng.randint(30, 60))

            for scene in day_scenes:
                _, scene_id, _, _, int_ext, day_night, scene_type, loc, cast, _ = scene
                lo, hi = SETUPS_PER_SCENE[scene_type]
                for s in range(rng.randint(lo, hi)):
                    setup_id = f"{scene_id}_{chr(65 + s)}"
                    extras = rng.choices([0, 3, 12, 35, 80], [0.5, 0.2, 0.15, 0.1, 0.05])[0]
                    mins = setup_minutes(rng, scene_type, int_ext, day_night, extras, dp_id)
                    actual = int(mins * 60)
                    start_ts = clock
                    end_ts = clock + timedelta(seconds=actual)
                    clock = end_ts + timedelta(minutes=rng.randint(3, 12))

                    out["setups"].append([
                        pid, day, scene_id, setup_id, start_ts, end_ts,
                        int(BASE_MINUTES[scene_type] * 60), actual, loc,
                        int_ext, day_night, scene_type, extras, dp_id,
                        rng.randint(45, 110),
                    ])

                    lens = rng.choice(LENSES)
                    t_stop = rng.choice([1.4, 2.0, 2.8, 4.0, 5.6])
                    iso = rng.choice([400, 800, 1600])
                    mean_takes = MEAN_TAKES[scene_type]
                    n_takes = max(1, min(18, round(rng.gammavariate(2.2, mean_takes / 2.2))))
                    circled_take = rng.randint(1, n_takes)

                    for t in range(1, n_takes + 1):
                        take_id = f"{setup_id}_t{t:02d}"
                        dur = round(rng.uniform(18, 165), 1)
                        status = rng.choices(
                            ["complete", "aborted", "false_start"], [0.84, 0.11, 0.05]
                        )[0]
                        if status != "complete":
                            dur = round(dur * rng.uniform(0.08, 0.4), 1)

                        out["takes"].append([
                            pid, day, scene_id, setup_id, t, take_id,
                            f"A{d + 1:03d}",
                            f"A{d + 1:03d}_C{len(out['takes']) % 999:03d}",
                            "10:00:00:00", "10:01:00:00", dur, lens, t_stop,
                            rng.choice(ND_FILTERS), iso, 24.0, "ARRI_ALEXA_35",
                            status,
                            1 if t == circled_take and status == "complete" else 0,
                            round(rng.uniform(0.86, 0.99), 3),
                            f"gs://the-gate-media/{take_id}.mp4",
                        ])

                        focus = round(min(1.0, rng.betavariate(9, 1.4)), 3)
                        expo = round(min(1.0, rng.betavariate(11, 1.2)), 3)
                        flags = []
                        if rng.random() < 0.09:
                            flags = rng.sample(CONTINUITY_FLAGS, rng.randint(1, 2))
                        plate_setup = scene_type == "vfx" and s == 0

                        out["take_analysis"].append([
                            pid, day, scene_id, setup_id, take_id,
                            rng.choice(SHOT_SIZES),
                            rng.choices(MOVEMENTS, MOVEMENT_WEIGHTS)[0],
                            rng.sample(cast, rng.randint(1, len(cast))),
                            rng.choice(["left", "right", "neutral"]),
                            rng.choice(cast), focus, expo, flags,
                            1 if plate_setup and rng.random() < 0.7 else 0,
                            1 if plate_setup and rng.random() < 0.8 else 0,
                            1 if plate_setup and rng.random() < 0.6 else 0,
                            1 if plate_setup and rng.random() < 0.5 else 0,
                            1 if plate_setup and rng.random() < 0.4 else 0,
                            "gemini-3.7-flash", start_ts,
                        ])

                        # per-second frames; transient defects live here
                        boom_at = rng.randint(0, max(1, int(dur))) if rng.random() < 0.07 else -1
                        soft_from = rng.randint(0, max(1, int(dur))) if rng.random() < 0.12 else -1
                        for sec in range(int(dur)):
                            f_score = focus
                            if 0 <= soft_from <= sec <= soft_from + 2:
                                f_score = round(focus * rng.uniform(0.35, 0.6), 3)
                            out["take_frames"].append([
                                day, scene_id, setup_id, take_id, sec,
                                f_score, expo, rng.choice(SHOT_SIZES), len(cast),
                                1 if sec == boom_at else 0,
                                1 if rng.random() < 0.004 else 0,
                            ])

    return out


COLUMNS = {
    "productions": ["production_id", "title", "kind", "start_date", "end_date",
                    "shoot_days", "primary_dp"],
    "scenes": ["production_id", "scene_id", "script_page", "page_eighths",
               "int_ext", "day_night", "scene_type", "location_id",
               "characters", "synopsis"],
    "scene_requirements": ["scene_id", "req_id", "shot_type", "subject",
                           "priority", "recover_cost_usd", "is_vfx_plate"],
    "shoot_days": ["production_id", "shoot_day", "unit", "location_id",
                   "call_time", "wrap_time", "sunrise", "sunset", "planned_scenes"],
    "setups": ["production_id", "shoot_day", "scene_id", "setup_id", "start_ts",
               "end_ts", "planned_duration_s", "actual_duration_s", "location_id",
               "int_ext", "day_night", "scene_type", "extras_count", "dp_id",
               "crew_size"],
    "takes": ["production_id", "shoot_day", "scene_id", "setup_id", "take_no",
              "take_id", "camera_roll", "clip_name", "tc_start", "tc_end",
              "duration_s", "lens_mm", "t_stop", "nd", "iso", "fps",
              "camera_body", "status", "circled", "slate_confidence", "proxy_uri"],
    "take_analysis": ["production_id", "shoot_day", "scene_id", "setup_id",
                      "take_id", "shot_size", "movement", "subjects",
                      "screen_direction", "eyeline_target", "focus_score",
                      "exposure_score", "continuity_flags", "vfx_clean_plate",
                      "vfx_chart", "vfx_grey_ball", "vfx_markers",
                      "vfx_lens_grid", "model_id", "analysed_at"],
    "take_frames": ["shoot_day", "scene_id", "setup_id", "take_id", "t_seconds",
                    "focus_score", "exposure_score", "shot_size",
                    "subjects_count", "boom_visible", "flicker"],
    "crew_hours": ["production_id", "shoot_day", "person_id", "department",
                   "call_ts", "wrap_ts", "meal_breaks", "is_minor", "union_local"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--productions", type=int, default=18)
    ap.add_argument("--seed", type=int, default=1948)
    ap.add_argument("--start", default="2022-03-01")
    ap.add_argument("--truncate", action="store_true")
    ap.add_argument("--batch", type=int, default=200_000)
    args = ap.parse_args()

    client = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        port=int(os.environ.get("CLICKHOUSE_PORT", 8443)),
        username=os.environ["CLICKHOUSE_USER"],
        password=os.environ["CLICKHOUSE_PASSWORD"],
        secure=True,
        database=DB,
    )

    if args.truncate:
        for table in COLUMNS:
            client.command(f"TRUNCATE TABLE IF EXISTS {DB}.{table}")
        client.command(f"TRUNCATE TABLE IF EXISTS {DB}.setup_duration_stats")
        print("truncated")

    data = generate(args.productions, date.fromisoformat(args.start), args.seed)

    for table, rows in data.items():
        if not rows:
            continue
        for i in range(0, len(rows), args.batch):
            client.insert(table, rows[i:i + args.batch], column_names=COLUMNS[table])
        print(f"{table:20s} {len(rows):>12,}")


if __name__ == "__main__":
    main()
