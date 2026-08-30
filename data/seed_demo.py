"""Make the demo day ready to be walked into.

A judge opens the hosted URL and has ninety seconds.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from dotenv import load_dotenv

from core import character_coverage as cc
from core import crew
from core.coverage import connect

load_dotenv()

DEMO = "prod_now"

# A small unit on a location horror shoot: a couple of actors, a camera
# department, and not much else.
CREW_TIER = "indie"

# What a scene with nobody on camera was for. The system can find a wide
# with nobody in it; only a person knows the wide *was* the point.
SCENE_GOALS: dict[str, list[str]] = {
    # a hand struggling with a handle: the close-up is the whole scene, and
    "door": ["insert"],
    "doorway": ["insert"],
    "hallway_doorway": ["insert"],
    # a chase with nobody's face visible still needs to show where it happens
    "stairwell": ["establisher"],
    "interior_stairwell": ["establisher"],
    "warehouse": ["establisher"],
    "loading_bay": ["establisher"],
}


def take_in(film: str) -> None:
    """Put a day's footage through the whole pipeline, into the demo."""
    import shutil
    from pathlib import Path

    from agents.intake import ingest_film, upload_dir
    from api.events import bus

    given = Path(film).expanduser().resolve()
    if not given.exists():
        raise SystemExit(f"No such file: {given}")

    # Staged the way an upload is, so the cut clips land where the player
    # looks for them. Running the pipeline on the file where it sits left the
    # clips beside it, and every take in the demo was unplayable.
    here = upload_dir(DEMO, "film")
    source = here / given.name
    shutil.copy2(given, source)

    ch = connect()
    print(f"clearing the demo, then taking in {source.name}")
    for table in ("scenes", "setups", "takes", "take_analysis",
                  "take_characters", "characters", "take_problems",
                  "production_world", "crew_plan"):
        ch.command(
            f"ALTER TABLE the_gate.{table} DELETE WHERE production_id = %(p)s",
            parameters={"p": DEMO}, settings={"mutations_sync": 2})
    ch.command(
        "ALTER TABLE the_gate.character_requirements DELETE "
        "WHERE startsWith(scene_id, %(p)s)",
        parameters={"p": DEMO}, settings={"mutations_sync": 2})

    run = bus.start(DEMO)
    ingest_film(source, DEMO, run)
    print("  footage taken in")


def demo_scenes(ch) -> list[tuple[str, str]]:
    return ch.query(
        f"SELECT scene_id, location_id FROM the_gate.scenes "
        f"WHERE production_id = %(p)s AND location_id != 'nothing_yet' "
        f"ORDER BY scene_id",
        parameters={"p": DEMO},
    ).result_rows


def set_crew(ch) -> None:
    crew.crew_table()
    tier = crew.BY_KEY[CREW_TIER]
    ch.insert("crew_plan", [[
        DEMO, tier.key, json.dumps(tier.departments), tier.cast, 0,
        tier.crew_rate, tier.cast_rate, datetime.now(),
    ]], column_names=["production_id", "tier", "departments", "cast", "minors",
                      "crew_rate", "cast_rate", "updated_at"])
    print(f"  crew        {tier.label} — {tier.crew_size} crew + {tier.cast} cast")


def set_goals(ch, scenes: list[tuple[str, str]]) -> None:
    """Say what the scenes with nobody on camera were for."""
    for scene_id, place in scenes:
        wants = SCENE_GOALS.get(place)
        if not wants:
            continue
        for shot in wants:
            ch.insert("character_requirements", [[
                scene_id, cc.SCENE_ROW, shot, 1, cc.SCENE_COST[shot],
                datetime.now(),
            ]], column_names=["scene_id", "character_id", "shot_type",
                              "required", "recover_cost_usd", "updated_at"])
        print(f"  goal        {place.replace('_', ' ')}: "
              f"{', '.join(cc.SCENE_LABEL[w].lower() for w in wants)}")


def where_it_stands(ch, scenes: list[tuple[str, str]]) -> None:
    required = have = exposure = unjudged = 0
    print()
    for scene_id, place in scenes:
        rows = cc.matrix(ch, scene_id)
        summary = cc.summarise(rows, cc.scene_shots(ch, scene_id))
        required += summary["required"]
        have += summary["have"]
        exposure += summary["exposure_usd"]
        unjudged += 0 if summary["judged"] else 1
        state = (f"{summary['completeness']:.0%}" if summary["judged"]
                 else "no goal")
        print(f"  {place.replace('_', ' '):<26} {len(rows)} on camera  "
              f"{summary['have']:>2}/{summary['required']:<2} {state:>8}")

    print(f"\n  the day: {have}/{required} shots, "
          f"{have / required:.0%} covered, ${exposure:,} to recover later")
    if unjudged:
        print(f"  {unjudged} scene(s) still with nothing asked of them")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="say where it stands without changing anything")
    ap.add_argument("--film", default="",
                    help="a day's footage to take in first, replacing the demo")
    args = ap.parse_args()

    if args.film:
        take_in(args.film)

    ch = connect()
    scenes = demo_scenes(ch)
    if not scenes:
        print("The demo has no scenes. Run the ingest first.")
        return

    print(f"{len(scenes)} scenes in the demo")
    if not args.check:
        set_crew(ch)
        set_goals(ch, scenes)

    where_it_stands(ch, scenes)


if __name__ == "__main__":
    main()
