"""One shoot day each.

Several people can have the interface open at once — judges, in our case — and
they must not shoot into each other's day. Every visitor gets a workspace,
identified by a cookie, and every row they create is stamped with it.

The seeded demo lives in its own workspace and is shared, read-only. The moment
a visitor changes anything, their workspace is forked from it: they get their
own copy of the scenes, the cast and the takes, and can rename, re-tick and
upload without touching anyone else. Nobody starts at an empty screen, and
nobody stands on anyone.
"""

from __future__ import annotations

import os
import uuid

from fastapi import Request, Response

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")

# The seeded day everyone starts from.
DEMO = "prod_now"

COOKIE = "gate_workspace"
COOKIE_MAX_AGE = 60 * 60 * 24 * 14

# Tables carrying a production_id, and the columns to copy when forking.
FORKED: dict[str, list[str]] = {
    "scenes": ["production_id", "scene_id", "script_page", "page_eighths",
               "int_ext", "day_night", "scene_type", "location_id",
               "characters", "synopsis"],
    "setups": ["production_id", "shoot_day", "scene_id", "setup_id", "start_ts",
               "end_ts", "planned_duration_s", "actual_duration_s",
               "location_id", "int_ext", "day_night", "scene_type",
               "extras_count", "dp_id", "crew_size"],
    "takes": ["production_id", "shoot_day", "scene_id", "setup_id", "take_no",
              "take_id", "camera_roll", "clip_name", "tc_start", "tc_end",
              "duration_s", "lens_mm", "t_stop", "nd", "iso", "fps",
              "camera_body", "status", "circled", "slate_confidence",
              "proxy_uri"],
    "take_analysis": ["production_id", "shoot_day", "scene_id", "setup_id",
                      "take_id", "shot_size", "movement", "subjects",
                      "screen_direction", "eyeline_target", "focus_score",
                      "exposure_score", "continuity_flags", "vfx_clean_plate",
                      "vfx_chart", "vfx_grey_ball", "vfx_markers",
                      "vfx_lens_grid", "model_id", "analysed_at"],
    "take_characters": ["production_id", "scene_id", "setup_id", "take_id",
                        "character_id", "confidence", "bbox", "prominence",
                        "matched_by"],
    "characters": ["production_id", "character_id", "name", "face_uri",
                   "embedding", "description", "first_take_id", "appearances",
                   "created_at"],
    "shoot_days": ["production_id", "shoot_day", "unit", "location_id",
                   "call_time", "wrap_time", "sunrise", "sunset",
                   "planned_scenes"],
    "crew_hours": ["production_id", "shoot_day", "person_id", "department",
                   "call_ts", "wrap_ts", "meal_breaks", "is_minor",
                   "union_local"],
}


def workspace_id(request: Request, response: Response | None = None) -> str:
    """This visitor's workspace, minted on first sight."""
    existing = request.cookies.get(COOKIE)
    if existing and existing.startswith("ws_"):
        return existing

    fresh = f"ws_{uuid.uuid4().hex[:10]}"
    if response is not None:
        response.set_cookie(
            COOKIE, fresh, max_age=COOKIE_MAX_AGE,
            httponly=True, samesite="lax",
        )
    return fresh


def has_own_copy(ch, workspace: str) -> bool:
    return bool(ch.query(
        f"SELECT count() FROM {DB}.scenes WHERE production_id = %(w)s",
        parameters={"w": workspace},
    ).result_rows[0][0])


def scene_for(ch, workspace: str, scene_id: str) -> str:
    """Translate a demo scene id into this workspace's copy of it."""
    if scene_id.startswith(workspace):
        return scene_id
    if scene_id.startswith(DEMO + "_") and has_own_copy(ch, workspace):
        return scene_id.replace(DEMO, workspace, 1)
    return scene_id


def character_for(ch, workspace: str, character_id: str) -> str:
    """Translate a demo character id into this workspace's copy of it.

    A visitor reads the shared demo, then changes something — by which point
    their fork exists and holds the same person under a different id. Without
    this, their edit lands on nothing.
    """
    if character_id.startswith(workspace):
        return character_id
    if has_own_copy(ch, workspace):
        return f"{workspace}_{character_id}"
    return character_id


def read_from(ch, workspace: str) -> str:
    """Which production to read: theirs once forked, the demo until then."""
    return workspace if has_own_copy(ch, workspace) else DEMO


def fork(ch, workspace: str) -> bool:
    """Give this visitor their own copy of the demo day.

    Called before the first change they make. Scene, setup and character ids
    are all rewritten into their workspace, so two people editing "scene 1" or
    renaming the same face are touching different rows. face_uri is left
    pointing at the original crop — the images are read-only and shared.
    """
    if has_own_copy(ch, workspace):
        return False

    for table, columns in FORKED.items():
        rows = ch.query(
            f"SELECT {', '.join(columns)} FROM {DB}.{table} "
            f"WHERE production_id = %(d)s",
            parameters={"d": DEMO},
        ).result_rows
        if not rows:
            continue

        rewritten = []
        for row in rows:
            values = list(row)
            values[0] = workspace
            for i, name in enumerate(columns):
                value = values[i]
                if name in ("scene_id", "setup_id") and isinstance(value, str):
                    values[i] = value.replace(DEMO, workspace, 1)
                elif name == "character_id" and isinstance(value, str):
                    values[i] = f"{workspace}_{value}"
                elif name == "planned_scenes" and value:
                    values[i] = [x.replace(DEMO, workspace, 1) for x in value]
            rewritten.append(values)

        ch.insert(table, rewritten, column_names=columns)

    # requirements are keyed by scene, not production
    needs = ch.query(
        f"""
        SELECT scene_id, character_id, shot_type, required, recover_cost_usd,
               updated_at
        FROM {DB}.character_requirements FINAL
        WHERE startsWith(scene_id, %(d)s)
        """,
        parameters={"d": DEMO},
    ).result_rows
    if needs:
        ch.insert(
            "character_requirements",
            [[r[0].replace(DEMO, workspace, 1), f"{workspace}_{r[1]}",
              r[2], r[3], r[4], r[5]] for r in needs],
            column_names=["scene_id", "character_id", "shot_type", "required",
                          "recover_cost_usd", "updated_at"],
        )

    return True
