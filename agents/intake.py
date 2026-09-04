"""Taking a day's footage in.

One file goes in and comes out as scenes, setups, takes, faces and faults, with
nobody typing a scene number. This is the pipeline that runs the crew over it,
in order:
"""

from __future__ import annotations

import os
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Any

from agents import casting, gemini
from agents.locations import merge_labels
from core import character_coverage as cc
from core.coverage import connect

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")

# How many shots to work on at once. Every one of these is a large upload
# waiting on a reply, so the processor is idle either way, the ceiling is what
# the model will accept in parallel, not what this machine can do.
AT_ONCE = int(os.environ.get("INTAKE_AT_ONCE", "4"))

# Below this a shot is short enough to be one take, and looking again is a
# model call spent to be told what we already know.
SECOND_LOOK_SECONDS = 25.0

# A take shorter than this is a fragment of one, not a take in its own right.
MIN_TAKE_SECONDS = 3.0


# Where footage lands. Locally a folder; deployed, a bucket prefix. Every
# upload gets its own directory, so two people dropping "scene1.mp4" cannot
# overwrite each other and a clip traces back to the upload it came from.
FOOTAGE_ROOT = Path(os.environ.get(
    "FOOTAGE_ROOT",
    Path(__file__).resolve().parents[1].parent / "footage",
))
CLIPS_DIR = FOOTAGE_ROOT / "clips"
CLIPS_DIR.mkdir(parents=True, exist_ok=True)


def upload_dir(workspace: str, kind: str) -> Path:
    """A fresh directory for one upload."""
    target = FOOTAGE_ROOT / "uploads" / workspace / f"{kind}_{uuid.uuid4().hex[:8]}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def joined_path(workspace: str, take_id: str) -> Path:
    """Where a clip this workspace made for itself lives.

    A day copied from another one shares its take ids and its footage. So a
    workspace that joins two takes writes the result here, under its own name,
    and never touches the file the other day is still playing.
    """
    return FOOTAGE_ROOT / "joined" / workspace / f"{take_id}.mp4"


def clip_path(take_id: str, workspace: str | None = None) -> Path:
    """Find a clip wherever it was uploaded to.

    Older takes live directly in clips/; newer ones sit under the upload that
    brought them in. Checking the flat folder first keeps the seeded demo fast.

    Anything this workspace joined for itself wins over both, because that is
    the version of the take it decided on.
    """
    if workspace:
        mine = joined_path(workspace, take_id)
        if mine.exists():
            return mine

    direct = CLIPS_DIR / f"{take_id}.mp4"
    if direct.exists():
        return direct
    for found in FOOTAGE_ROOT.rglob(f"{take_id}.mp4"):
        return found
    return direct


def recheck_scene(scene_id: str, production_id: str, run) -> None:
    from agents import qc
    from google import genai

    ch = connect()
    gclient = gemini.client()
    period, setting, notes = qc.world_of(ch, production_id)

    ch.command(
        f"ALTER TABLE {DB}.take_problems DELETE WHERE scene_id = %(s)s",
        parameters={"s": scene_id},
    )

    takes = ch.query(
        f"SELECT setup_id, take_id FROM {DB}.takes WHERE scene_id = %(s)s "
        f"ORDER BY setup_id, take_no",
        parameters={"s": scene_id},
    ).result_rows

    flagged = 0
    for setup_id, take_id in takes:
        path = clip_path(take_id)
        if not path.exists():
            continue
        run.publish("qc", "working", f"Checking {take_id}")
        try:
            verdict = qc.check_take(gclient, path, period, setting, notes)
        except Exception as exc:
            run.publish("qc", "error", f"{take_id}: {type(exc).__name__}")
            continue

        qc.store(ch, production_id, scene_id, setup_id, take_id, verdict)
        blocking = [p for p in verdict.get("problems", [])
                    if p["severity"] == "blocking"]
        if blocking:
            flagged += 1
            run.publish("qc", "tool_result",
                        f"{take_id}: {blocking[0]['what'][:70]}")

    # everything above judges takes one at a time; this is the only check that
    run.publish("continuity", "working", "Checking the angles cut together")
    try:
        from agents import continuity
        verdict = continuity.compare_scene(gclient, ch, scene_id, CLIPS_DIR)
        if verdict:
            continuity.store(ch, production_id, scene_id, verdict)
            run.publish(
                "continuity", "tool_result",
                verdict["one_line"][:110] if not verdict["will_cut"]
                else "The angles cut together",
            )
    except Exception as exc:
        run.publish("continuity", "error", type(exc).__name__)

    run.publish("orchestrator", "done",
                f"{flagged} of {len(takes)} takes can't be used")
    run.finish()


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


def ingest_clips(paths: list[Path], scene_id: str, setup_hint: str, run,
                 finish: bool = True, precomputed: dict | None = None,
                 faults: dict | None = None, faces: dict | None = None) -> None:
    """Wrapper so a failure is reported rather than swallowed by the thread."""
    try:
        _run_clips(paths, scene_id, setup_hint, run, finish, precomputed,
                   faults, faces)
    except Exception as exc:
        run.publish("orchestrator", "error",
                    f"{type(exc).__name__}: {exc}"[:300])
        if finish:
            run.finish()


def _run_clips(paths: list[Path], scene_id: str, setup_hint: str, run,
                  finish: bool = True, precomputed: dict | None = None,
                  faults: dict | None = None, faces: dict | None = None) -> None:
    """Watch each clip, find the faces, write it all down.

    `faults` and `faces` let a whole film hand in work already done. Called per
    setup, the pools here had one take to work on: parallelism inside a group
    of one is nothing, and a twenty-five shot film reported "checking 1 takes"
    eleven times over.
    """
    from agents.vision import analyse_clip
    from google import genai

    ch = connect()
    gclient = gemini.client()

    row = ch.query(
        f"SELECT production_id, location_id, int_ext, day_night "
        f"FROM {DB}.scenes WHERE scene_id = %(s)s",
        parameters={"s": scene_id},
    ).result_rows
    production_id = row[0][0] if row else "prod_now"
    # A setup is a camera position inside a scene, so it is in the same place
    # at the same time by definition. It takes those from the scene rather
    # than deciding again per take and disagreeing with its neighbours.
    int_ext = row[0][2] if row else "INT"
    day_night = row[0][3] if row else "DAY"
    shoot_day = date.today()

    # QC cannot call anything an anachronism without knowing the world the
    # production is set in.
    from agents.qc import world_of
    period, setting, world_notes = world_of(ch, production_id)
    if not setting:
        here = ch.query(
            f"SELECT replaceAll(location_id, '_', ' ') FROM {DB}.scenes "
            f"WHERE scene_id = %(s)s LIMIT 1",
            parameters={"s": scene_id},
        ).result_rows
        setting = here[0][0] if here else ""

    looking: list[tuple[str, Path, float, str]] = []

    for path in paths:
        take_id = path.stem
        duration = duration_of(path)

        analysis = (precomputed or {}).get(take_id)
        if analysis is None:
            run.publish("vision", "working", f"Watching {take_id}")
            analysis = analyse_clip(gclient, {
                "clip_name": take_id, "camera_roll": take_id.split("_")[0],
                "duration_s": duration, "start_s": 0.0, "path": str(path),
            })
        run.publish("vision", "tool_result",
                    f"{take_id}: {analysis['shot_size']}, "
                    f"{analysis['subjects_count']} in frame")

        # Dropped onto a setup, it belongs there. Dropped loose, it gets a
        # setup of its own named after the framing, so nothing is silently
        # lumped in with an unrelated camera position.
        setup_id = setup_hint or f"{scene_id}_{analysis['shot_size']}"

        # A setup row is written whether the camera position was worked
        # out here or handed in by the film pass.
        exists = ch.query(
            f"SELECT count() FROM {DB}.setups WHERE setup_id = %(u)s",
            parameters={"u": setup_id},
        ).result_rows[0][0]
        if not exists:
            # The framing comes off the footage, because it is what the
            # position costs: a wide means lighting the whole space, a
            # close-up means moving in on one that is already lit.
            ch.insert("setups", [[
                production_id, shoot_day, scene_id, setup_id,
                datetime.now(), None, 2400, 0,
                row[0][1] if row else "location", int_ext, day_night,
                "dialogue", 0, "dp_lind", 62,
                analysis.get("shot_size", "") or "",
            ]], column_names=[
                "production_id", "shoot_day", "scene_id", "setup_id",
                "start_ts", "end_ts", "planned_duration_s",
                "actual_duration_s", "location_id", "int_ext", "day_night",
                "scene_type", "extras_count", "dp_id", "crew_size",
                "shot_size"])
        take_no = ch.query(
            f"SELECT count() + 1 FROM {DB}.takes WHERE setup_id = %(u)s",
            parameters={"u": setup_id},
        ).result_rows[0][0]

        ch.insert("takes", [[
            production_id, shoot_day, scene_id, setup_id, take_no, take_id,
            take_id.split("_")[0], take_id, "10:00:00:00", "10:00:00:00",
            duration, 0.0, 0.0, "none", 800, 24.0, "SONY_F65", "complete", 0,
            0.0, f"file://../footage/clips/{path.name}",
        ]], column_names=[
            "production_id", "shoot_day", "scene_id", "setup_id", "take_no",
            "take_id", "camera_roll", "clip_name", "tc_start", "tc_end",
            "duration_s", "lens_mm", "t_stop", "nd", "iso", "fps", "camera_body",
            "status", "circled", "slate_confidence", "proxy_uri"])

        ch.insert("take_analysis", [[
            production_id, shoot_day, scene_id, setup_id, take_id,
            analysis["shot_size"], analysis["movement"],
            analysis.get("subjects", []), analysis["screen_direction"],
            analysis.get("eyeline_target", ""), float(analysis["focus_score"]),
            float(analysis["exposure_score"]),
            analysis.get("technical_faults", []),
            int(bool(analysis.get("vfx_clean_plate"))),
            int(bool(analysis.get("vfx_chart_visible"))), 0,
            int(bool(analysis.get("vfx_markers_visible"))), 0,
            analysis.get("model_id", ""), datetime.now(),
        ]], column_names=[
            "production_id", "shoot_day", "scene_id", "setup_id", "take_id",
            "shot_size", "movement", "subjects", "screen_direction",
            "eyeline_target", "focus_score", "exposure_score",
            "continuity_flags", "vfx_clean_plate", "vfx_chart", "vfx_grey_ball",
            "vfx_markers", "vfx_lens_grid", "model_id", "analysed_at"])

        looking.append((take_id, path, duration, setup_id))

    # The two slow passes over every take, run together rather than one take at
    # a time. Both are almost entirely spent waiting on a reply.
    def check(job):
        take_id, path, _duration, setup_id = job
        try:
            verdict = qc.check_take(gclient, path, period, setting, world_notes)
            return take_id, setup_id, verdict, None
        except Exception as exc:
            return take_id, setup_id, None, exc

    def look(job):
        take_id, path, duration, _setup_id = job
        try:
            return take_id, casting.people_in_take(gclient, path, duration or 3.0)
        except Exception:
            return take_id, []

    from agents import qc

    if faults is not None and faces is not None:
        # the whole film was checked in one pool before we got here
        checked = [(t, su, faults.get(t), None)
                   for t, _p, _d, su in looking if faults.get(t)]
        seen = faces
    else:
        run.publish("qc", "working",
                    f"Checking {len(looking)} takes for problems")
        with ThreadPoolExecutor(max_workers=AT_ONCE) as pool:
            checked = list(pool.map(check, looking))
            run.publish("casting", "working",
                        f"Looking for faces in {len(looking)} takes")
            seen = dict(pool.map(look, looking))

    for take_id, setup_id, verdict, failed in checked:
        if failed is not None:
            run.publish("qc", "error", f"{take_id}: {type(failed).__name__}")
            continue
        qc.store(ch, production_id, scene_id, setup_id, take_id, verdict)
        blocking = [p for p in verdict.get("problems", [])
                    if p["severity"] == "blocking"]
        run.publish(
            "qc", "tool_result",
            (f"{take_id}: {blocking[0]['what'][:70]}" if blocking
             else f"{take_id}: clean"),
            {"usable": verdict.get("usable", True),
             "problems": len(verdict.get("problems", []))},
        )

    # One at a time on purpose: each take is matched against the people found
    # so far, and two running together would both decide the same person was
    # someone new.
    for take_id, path, duration, setup_id in looking:
        known = ch.query(
            f"SELECT count() FROM {DB}.characters WHERE production_id = %(p)s",
            parameters={"p": production_id},
        ).result_rows[0][0]
        links = casting.analyse_take(gclient, ch, production_id, scene_id,
                                     setup_id, take_id, path, duration, known,
                                     sightings=seen.get(take_id))
        fresh = sum(1 for l in links if l["matched_by"] == "new")
        run.publish("casting", "tool_result",
                    f"{take_id}: {len(links)} face(s), {fresh} new"
                    if links else f"{take_id}: no faces found")

    run.publish("continuity", "working", "Checking the angles cut together")
    try:
        from agents import continuity
        verdict = continuity.compare_scene(gclient, ch, scene_id, CLIPS_DIR)
        if verdict:
            continuity.store(ch, production_id, scene_id, verdict)
            run.publish(
                "continuity", "tool_result",
                verdict["one_line"][:110] if not verdict["will_cut"]
                else "The angles cut together",
            )
    except Exception as exc:
        run.publish("continuity", "error", type(exc).__name__)

    if finish:
        run.publish("orchestrator", "done", f"{len(paths)} clip(s) taken in",
                    {"scenes": [scene_id], "shots": len(paths)})
        run.finish()


NEW_SETUP_SIGNS = ("repositioned", "new location", "new angle", "cut to",
                   "different angle", "moved")


def starts_new_setup(what_changed: str) -> bool:
    return any(sign in (what_changed or "").lower() for sign in NEW_SETUP_SIGNS)


def resets_within(shot: dict, source: Path, run) -> list[dict]:
    """One shot, watched again on its own, in case it is several takes.

    The first pass reads five minutes at a time, and a reset inside that — the
    crew going again on the same action — is easy to read straight past. Asked
    about a forty-second clip on its own it is an easy question, and it is the
    same agent answering, so nothing new has to be trusted.

    This was tried once before the previews carried sound and it split
    everything it was shown, because without the calls of action and cut a
    camera move is the only thing that looks like a boundary. With the audio
    it leaves most clips alone and names its evidence when it does not.
    """
    from agents import editor
    from data.split_takes import cut

    if shot["seconds"] < SECOND_LOOK_SECONDS:
        return [shot]

    clip = source.parent / f"_look_{int(shot['starts_at'] * 100)}.mp4"
    try:
        cut(source, clip, shot["starts_at"], shot["ends_at"])
        found = editor.find_shots(gemini.client(), clip)
    except Exception:
        return [shot]
    finally:
        clip.unlink(missing_ok=True)

    inner = [f for f in found if f["seconds"] >= MIN_TAKE_SECONDS]
    if len(inner) < 2:
        return [shot]

    run.publish("editor", "tool_result",
                f"{shot.get('description') or 'one shot'}"[:32]
                + f": {len(inner)} takes, not one")

    # Timings come back relative to the clip, so they move back onto the film.
    return [{
        **shot,
        "starts_at": shot["starts_at"] + f["starts_at"],
        "ends_at": shot["starts_at"] + f["ends_at"],
        "seconds": f["seconds"],
        "what_changed": f["what_changed"] if i else shot["what_changed"],
        "is_slate": bool(f.get("is_slate")),
        "description": f.get("description") or shot.get("description", ""),
    } for i, f in enumerate(inner)]


def split_into_shots(source: Path, run) -> list[dict]:
    """Cut a long file into shots, deciding the boundaries by watching it.

A whole film is not a take. Frame-difference detection finds the obvious
    hard cuts and misses everything else, measured on real footage it found
    five shots in twenty-two minutes, one of them seventeen minutes long.
    """
    from google import genai

    from agents import editor
    from data.split_takes import cut, duration_of
    from data.split_takes import find_shots as detector_shots

    total = duration_of(source)
    run.publish("editor", "working",
                f"Watching {source.name}, {total / 60:.0f} minutes")

    # cheap candidates first; the model never sees them, so it cannot simply
    # agree with the detector, but nothing obvious is lost either
    try:
        marks = detector_shots(source, total)
        candidates = marks[1:-1]
    except Exception:
        candidates = []

    shots = editor.find_shots(
        gemini.client(), source, candidates,
        on_step=lambda at, whole: run.publish(
            "editor", "working",
            f"Watching {at / 60:.0f}–"
            f"{min(at + editor.WINDOW_SECONDS, whole) / 60:.0f} min"),
    )

    slates = sum(1 for s in shots if s.get("is_slate"))
    run.publish("editor", "tool_result",
                f"{len(shots)} shots"
                + (f", {slates} marked with a slate" if slates else ""))

    shots = [piece for shot in shots for piece in resets_within(shot, source, run)]

    roll = f"U{uuid.uuid4().hex[:3].upper()}"
    made: list[dict] = []
    setup_index = 0

    for shot in shots:
        if shot["seconds"] < 1.5:
            continue
        if not made or starts_new_setup(shot["what_changed"]):
            setup_index += 1

        out = source.parent / f"{roll}_C{len(made) + 1:03d}.mp4"
        cut(source, out, shot["starts_at"], shot["ends_at"])
        made.append({
            "path": out,
            "setup_index": setup_index,
            "why": shot["what_changed"],
            "is_slate": bool(shot.get("is_slate")),
            "seconds": shot["seconds"],
        })

    run.publish("editor", "tool_result",
                f"{len(made)} shots across {setup_index} camera positions")
    return made


# Widest first: a long shot shows the room, an extreme close-up shows a hand.
HOW_WIDE = ["ELS", "LS", "MLS", "MS", "MCU", "CU", "ECU"]


def widest_per_label(clips: list[Path], analyses: dict) -> dict[str, Path]:
    """One clip per location label — the one that shows the most of the place."""
    best: dict[str, tuple[int, Path]] = {}
    for clip in clips:
        analysis = analyses.get(clip.stem, {})
        label = (analysis.get("location_label") or "unsorted").strip().lower()
        size = analysis.get("shot_size", "")
        rank = HOW_WIDE.index(size) if size in HOW_WIDE else len(HOW_WIDE)
        if label not in best or rank < best[label][0]:
            best[label] = (rank, clip)
    return {label: clip for label, (_, clip) in best.items()}


def place_by_location(ch, run, workspace: str, clips: list[Path],
                       analyses: dict, minutes: float = 0.0) -> dict[str, str]:
    """Group shots into scenes by where they were filmed.

Nobody tells us where a scene starts and ends.
    """
    existing = {
        r[1].replace("_", " "): r[0] for r in ch.query(
            f"SELECT scene_id, location_id FROM {DB}.scenes "
            f"WHERE production_id = %(p)s",
            parameters={"p": workspace},
        ).result_rows
    }
    used = [int(sid.split("sc")[-1]) for sid in existing.values()] or [0]
    nxt = max(used) + 1

    raw = [(analyses.get(c.stem, {}).get("location_label") or "unsorted")
           for c in clips]

    # A frame for each label, so the decision is made on what the place looks
    # like and not only on how it was described — but it has to be a frame
    # that shows the place.
    #
    # This took the first clip carrying each label and grabbed a second in.
    # On camera-card footage a second in is the clapperboard, and the first
    # clip is as likely to be an insert as anything: two of the labels being
    # placed came through as a close-up of a slate and a close-up of an axe.
    # Asked whether those were the same room as a wide of a corridor, the
    # model could only guess, and it guessed differently depending on how the
    # prompt was worded. The widest shot carrying a label is the one that
    # shows the room, and the middle of it is past the slate.
    from agents import continuity as _cont

    frames: dict[str, bytes] = {}
    for label, clip in widest_per_label(clips, analyses).items():
        shot = _cont.grab_frame(clip, max(1.0, duration_of(clip) * 0.45))
        if shot:
            frames[label] = shot

    same_place = merge_labels(raw, known=list(existing), shots=len(clips),
                              minutes=minutes, frames=frames)
    merged = len(set(raw)) - len(set(same_place.values()))
    if merged > 0:
        run.publish("script", "tool_result",
                    f"{len(set(raw))} descriptions, {len(set(same_place.values()))} "
                    f"actual places")

    # A scene is one place at one time, so int/ext and day/night belong to the
    # scene and not to the shot. Asked per clip, a dim interior came back NIGHT
    # for two setups in a hallway and DAY for the third, which no call sheet
    # would ever say. Every clip in the place votes; the scene takes the answer.
    votes: dict[str, list[dict]] = {}
    for clip in clips:
        analysis = analyses.get(clip.stem, {})
        label = (analysis.get("location_label") or "unsorted").strip().lower()
        votes.setdefault(same_place.get(label, label), []).append(analysis)

    def agreed(place: str, field: str, fallback: str) -> str:
        said = [a.get(field) for a in votes.get(place, []) if a.get(field)]
        if not said:
            return fallback
        # Ties go to whichever was shot first, so the same footage twice gives
        # the same answer twice. set() iteration would not.
        return max(said, key=lambda v: (said.count(v), -said.index(v)))

    placed: dict[str, str] = {}
    for clip in clips:
        analysis = analyses.get(clip.stem, {})
        label = (analysis.get("location_label") or "unsorted").strip().lower()
        place = same_place.get(label, label)

        if place not in existing:
            scene_id = f"{workspace}_sc{nxt:03d}"
            ch.insert("scenes", [[
                workspace, scene_id, float(nxt), 12,
                agreed(place, "int_ext", "INT"),
                agreed(place, "day_night", "DAY"), "dialogue",
                place.replace(" ", "_")[:60],
                [], analysis.get("scene_summary", "")[:180],
            ]], column_names=["production_id", "scene_id", "script_page",
                              "page_eighths", "int_ext", "day_night",
                              "scene_type", "location_id", "characters",
                              "synopsis"])
            existing[place] = scene_id
            nxt += 1
            run.publish("script", "tool_result", f"New scene: {place}")

        placed[clip.stem] = existing[place]

    return placed


def ingest_film(source: Path, workspace: str, run) -> None:
    """Wrapper so a failure is reported rather than swallowed by the thread."""
    try:
        _run_film(source, workspace, run)
    except Exception as exc:
        run.publish("orchestrator", "error",
                    f"Could not take in {source.name}: {type(exc).__name__}, {exc}"[:300])
        run.finish()


def _run_film(source: Path, workspace: str, run) -> None:
    from agents.vision import analyse_clip
    from google import genai

    from data.split_takes import duration_of

    ch = connect()
    gclient = gemini.client()
    minutes = duration_of(source) / 60

    pieces = split_into_shots(source, run)
    if not pieces:
        run.publish("orchestrator", "error", "No shots found in that file")
        run.finish()
        return

    clips = [p["path"] for p in pieces]
    setup_of = {p["path"].stem: p["setup_index"] for p in pieces}

    # Every shot is watched independently, nothing about shot four depends
    # on shot three, and each one is a large upload the process spends
    # almost all its time waiting on.
    analyses: dict[str, dict] = {}
    watched = 0

    def watch(clip: Path) -> tuple[str, dict | None]:
        try:
            return clip.stem, analyse_clip(gclient, {
                "clip_name": clip.stem, "camera_roll": clip.stem.split("_")[0],
                "duration_s": duration_of(clip), "start_s": 0.0,
                "path": str(clip),
            })
        except Exception as exc:
            run.publish("vision", "error", f"{clip.stem}: {type(exc).__name__}")
            return clip.stem, None

    run.publish("vision", "working",
                f"Watching {len(clips)} shots, {AT_ONCE} at a time")
    with ThreadPoolExecutor(max_workers=AT_ONCE) as pool:
        for name, analysis in pool.map(watch, clips):
            watched += 1
            if analysis is not None:
                analyses[name] = analysis
                run.publish("vision", "tool_result",
                            f"{name}: {analysis['shot_size']}, "
                            f"{analysis.get('subjects_count', 0)} in frame",
                            {"done": watched, "of": len(clips)})

    # Work out the world before anything is judged against it, using frames
    # from across the film rather than one shot.
    run.publish("qc", "working", "Working out what world this is set in")
    try:
        from agents import continuity, qc
        sample = [c for c in clips[:: max(1, len(clips) // 6)]][:6]
        frames = [f for f in (continuity.grab_frame(c, 1.0) for c in sample) if f]
        if frames:
            guess = qc.infer_world(gclient, frames)
            ch.insert("production_world", [[
                workspace, guess.get("period", "")[:200],
                guess.get("setting", "")[:200], guess.get("notes", "")[:400],
                datetime.now(),
            ]], column_names=["production_id", "period", "setting", "notes",
                              "updated_at"])
            run.publish("qc", "tool_result",
                        f"Set in {guess.get('period', 'unknown')}"
                        f" — {guess.get('setting', '')}"[:120])
    except Exception as exc:
        run.publish("qc", "error", f"Could not read the world: {type(exc).__name__}")

    run.publish("script", "working", "Sorting the shots into scenes")
    placed = place_by_location(ch, run, workspace, clips, analyses,
                                minutes=minutes)

    by_scene: dict[str, list[Path]] = {}
    for clip in clips:
        scene_id = placed.get(clip.stem)
        if scene_id:
            by_scene.setdefault(scene_id, []).append(clip)

    # Every take in the film, checked and looked at in one pool. Doing this per
    # setup meant most pools held a single take and the waiting was spent once
    # per shot all over again.
    from agents import qc as _qc

    faults: dict[str, dict] = {}
    faces: dict[str, list] = {}
    world = _qc.world_of(ch, workspace)

    def check_one(clip: Path):
        try:
            return clip.stem, _qc.check_take(gclient, clip, *world)
        except Exception as exc:
            run.publish("qc", "error", f"{clip.stem}: {type(exc).__name__}")
            return clip.stem, None

    def look_one(clip: Path):
        try:
            return clip.stem, casting.people_in_take(
                gclient, clip, duration_of(clip) or 3.0)
        except Exception:
            return clip.stem, []

    run.publish("qc", "working",
                f"Checking {len(clips)} takes, {AT_ONCE} at a time")
    with ThreadPoolExecutor(max_workers=AT_ONCE) as pool:
        for name, verdict in pool.map(check_one, clips):
            if verdict:
                faults[name] = verdict
        run.publish("casting", "working",
                    f"Looking for faces in {len(clips)} takes, "
                    f"{AT_ONCE} at a time")
        faces = dict(pool.map(look_one, clips))

    for scene_id, group in by_scene.items():
        # keep the Editor's grouping: shots it called "camera stopped and
        by_setup: dict[int, list[Path]] = {}
        for clip in group:
            by_setup.setdefault(setup_of.get(clip.stem, 0), []).append(clip)

        for index, takes in sorted(by_setup.items()):
            ingest_clips(takes, scene_id, f"{scene_id}_{chr(64 + max(1, index))}",
                         run, finish=False, precomputed=analyses,
                         faults=faults, faces=faces)

    made = sorted(by_scene)
    run.publish(
        "orchestrator", "done",
        f"{len(clips)} shots across {len(by_scene)} scenes",
        {"scenes": made, "shots": len(clips)},
    )
    run.finish()


