"""What a scene still needs.

The gate answers "can we move on".
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")

BANDS = ["wide", "medium", "close", "over"]

BAND_LABEL = {
    "wide": "wide",
    "medium": "medium",
    # The band, not one size in it. A medium close-up covers a character's
    # close as surely as a close-up does, and calling the band "close-up"
    # read as a contradiction against a take the log had just called MCU.
    "close": "close",
    "over": "over-the-shoulder",
}

BAND_HELP = {
    "wide": "shows where they are",
    "medium": "waist up",
    "close": "the face, for lines and reactions",
    "over": "past one shoulder onto the other — how a conversation cuts",
}

SIZE_TO_BAND = {
    "ELS": "wide", "LS": "wide", "MLS": "wide",
    "MS": "medium",
    "MCU": "close", "CU": "close", "ECU": "close",
}

# What we assume every speaking character needs, before the AD edits it.
DEFAULT_REQUIRED = {"wide": True, "medium": False, "close": True, "over": True}

# What coming back for one costs, as a share of a day with this unit.
#
# A pickup is a day: you recall the people, you get the location again, and
# you shoot one thing. What varies is who you have to bring back. A close-up
# means the actor — their availability is the expensive part, and they may be
# on another job. A wide might be got with a double. An insert of a hand or a
# door handle needs neither, which is exactly why it is the shot most often
# let go.
#
# A share rather than a figure because a pickup with three people and a camera
# is not a pickup with a hundred and forty. These were flat, so the headline
# "at risk" number did not move when the crew did — a studio unit and a two
# person crew were quoted the same thirty thousand for the same missing
# close-up.
PICKUP_SHARE = {"wide": 0.50, "medium": 0.38, "close": 0.75, "over": 0.45}

# Shots that belong to the scene rather than to anyone in it. The scene is the
# subject: the room, the door handle, the empty frame the effects go into.
SCENE_ROW = "_scene"

SCENE_SHOTS = ["establisher", "insert", "plate"]

SCENE_LABEL = {
    "establisher": "A wide of the place",
    "insert": "A close-up of a detail",
    "plate": "The empty scene",
}

SCENE_HELP = {
    "establisher": "so the audience knows where they are",
    "insert": "a hand, a door handle, an object — nobody's face in it",
    "plate": "nobody in shot, so effects can be added later",
}

# The same, for the shots that belong to the scene rather than to anyone in
# it. A plate is a full day of the whole unit because the effects vendor is
# waiting on it; an insert is a tenth of one.
SCENE_SHARE = {"establisher": 0.50, "insert": 0.10, "plate": 1.00}

# Nobody has to be in these, and for an insert nobody should be.
WIDE_SIZES = {"ELS", "LS", "MLS"}
TIGHT_SIZES = {"CU", "ECU"}

# Someone glimpsed in the background is not in the scene, but that is what
# `prominence` already says, so it is the only test.


@dataclass
class Cell:
    band: str
    required: bool
    takes: list[str] = field(default_factory=list)
    recover_cost_usd: int = 0

    @property
    def have(self) -> bool:
        return bool(self.takes)

    @property
    def state(self) -> str:
        if self.have:
            return "have"
        return "missing" if self.required else "skip"


@dataclass
class SceneRow:
    """What the scene itself needs, apart from anyone in it."""
    cells: dict[str, Cell]

    @property
    def missing(self) -> list[Cell]:
        return [c for c in self.cells.values() if c.state == "missing"]

    @property
    def asked_for(self) -> bool:
        return any(c.required for c in self.cells.values())


@dataclass
class CharacterRow:
    character_id: str
    name: str
    face_uri: str
    appearances: int
    cells: dict[str, Cell]

    @property
    def missing(self) -> list[Cell]:
        return [c for c in self.cells.values() if c.state == "missing"]

    @property
    def complete(self) -> bool:
        return not self.missing


def _band_of(shot_size: str) -> str | None:
    return SIZE_TO_BAND.get(shot_size)


def pickup(client, scene_id: str, day_rate: float | None = None) -> float:
    """What a day with this unit costs, which is what a pickup is a share of."""
    if day_rate is not None:
        return day_rate
    from core.crew import day_rate as rate_of
    row = client.query(
        f"SELECT any(production_id) FROM {DB}.scenes WHERE scene_id = %(s)s",
        parameters={"s": scene_id},
    ).result_rows
    return rate_of(row[0][0] if row and row[0][0] else "prod_now")


def matrix(client, scene_id: str,
           day_rate: float | None = None) -> list[CharacterRow]:
    """Who is in the scene, and what we have on each of them."""
    cast = client.query(
        f"""
        SELECT c.character_id, c.name, c.face_uri, c.appearances
        FROM {DB}.characters AS c FINAL
        WHERE c.production_id = (
            SELECT any(production_id) FROM {DB}.scenes WHERE scene_id = %(s)s
        )
        AND c.character_id IN (
            SELECT character_id FROM {DB}.take_characters
            WHERE scene_id = %(s)s AND prominence = 'foreground'
        )
        ORDER BY c.appearances DESC
        """,
        parameters={"s": scene_id},
    ).result_rows

    # take -> shot size, and how many foreground people were in it.
    #
    # Counted separately rather than as a subquery inside the take query:
    heads = {
        r[0]: r[1] for r in client.query(
            f"""
            SELECT take_id, count() FROM {DB}.take_characters
            WHERE scene_id = %(s)s AND prominence = 'foreground'
            GROUP BY take_id
            """,
            parameters={"s": scene_id},
        ).result_rows
    }
    takes = {
        r[0]: {"size": r[1], "people": heads.get(r[0], 0)}
        for r in client.query(
            f"""
            SELECT a.take_id, a.shot_size FROM {DB}.take_analysis AS a
            WHERE a.scene_id = %(s)s
            """,
            parameters={"s": scene_id},
        ).result_rows
    }

    # A take with a blocking problem is not coverage. The footage exists; it
    # cannot be used, which is a different thing, and the difference is exactly
    # what stops a scene being wrapped short.
    blocked = {
        r[0] for r in client.query(
            f"""
            SELECT DISTINCT take_id FROM {DB}.take_problems
            WHERE scene_id = %(s)s AND severity = 'blocking'
            """,
            parameters={"s": scene_id},
        ).result_rows
    }

    appearances = client.query(
        f"""
        SELECT character_id, groupArray(take_id)
        FROM {DB}.take_characters
        WHERE scene_id = %(s)s AND prominence = 'foreground'
        GROUP BY character_id
        """,
        parameters={"s": scene_id},
    ).result_rows
    by_character = {r[0]: list(r[1]) for r in appearances}

    overrides = {
        (r[0], r[1]): (bool(r[2]), int(r[3]))
        for r in client.query(
            f"""
            SELECT character_id, shot_type, required, recover_cost_usd
            FROM {DB}.character_requirements FINAL WHERE scene_id = %(s)s
            """,
            parameters={"s": scene_id},
        ).result_rows
    }

    # An over-the-shoulder is a shot past one person onto another, so it
    # only exists where there is another person.
    alone = len(cast) < 2

    rows: list[CharacterRow] = []
    for character_id, name, face_uri, _production_wide in cast:
        # how many takes of *this scene* they are in, the production-wide
        here = by_character.get(character_id, [])

        cells: dict[str, Cell] = {}
        for band in BANDS:
            wanted = DEFAULT_REQUIRED[band]
            if band == "over" and alone:
                wanted = False
            required, cost = overrides.get((character_id, band), (wanted, 0))
            if not cost:
                cost = round(pickup(client, scene_id, day_rate)
                             * PICKUP_SHARE[band])
            cells[band] = Cell(band=band, required=required, recover_cost_usd=cost)

        for take_id in here:
            take = takes.get(take_id)
            if not take or take_id in blocked:
                continue
            band = _band_of(take["size"])
            if band is None:
                continue

            # An over-the-shoulder is two people in a tight or medium frame.
            if take["people"] == 2 and band in {"close", "medium"}:
                cells["over"].takes.append(take_id)
            # A close-up only counts as theirs if they are alone in it.
            if band == "close" and take["people"] > 1:
                continue
            cells[band].takes.append(take_id)

        rows.append(CharacterRow(
            character_id=character_id, name=name, face_uri=face_uri,
            appearances=len(here), cells=cells,
        ))

    return rows


def scene_shots(client, scene_id: str,
                day_rate: float | None = None) -> SceneRow:
    """Shots of the scene itself, and which takes satisfy them.

    Required only where someone has said so. A door-knob scene is not short of
    a wide and a close-up of nobody; it needs one insert, and the AD is the one
    who knows that.
    """
    wanted = {
        r[0]: (bool(r[1]), int(r[2]))
        for r in client.query(
            f"""
            SELECT shot_type, required, recover_cost_usd
            FROM {DB}.character_requirements FINAL
            WHERE scene_id = %(s)s AND character_id = %(c)s
            """,
            parameters={"s": scene_id, "c": SCENE_ROW},
        ).result_rows
    }

    blocked = {
        r[0] for r in client.query(
            f"SELECT DISTINCT take_id FROM {DB}.take_problems "
            f"WHERE scene_id = %(s)s AND severity = 'blocking'",
            parameters={"s": scene_id},
        ).result_rows
    }
    peopled = {
        r[0] for r in client.query(
            f"SELECT DISTINCT take_id FROM {DB}.take_characters "
            f"WHERE scene_id = %(s)s AND prominence = 'foreground'",
            parameters={"s": scene_id},
        ).result_rows
    }

    takes = client.query(
        f"""
        SELECT take_id, shot_size, vfx_clean_plate FROM {DB}.take_analysis
        WHERE scene_id = %(s)s
        """,
        parameters={"s": scene_id},
    ).result_rows

    cells = {
        shot: Cell(band=shot,
                   required=wanted.get(shot, (False, 0))[0],
                   recover_cost_usd=wanted.get(shot, (False, 0))[1]
                                    or round(pickup(client, scene_id, day_rate)
                                             * SCENE_SHARE[shot]))
        for shot in SCENE_SHOTS
    }

    for take_id, size, clean_plate in takes:
        if take_id in blocked:
            continue
        if size in WIDE_SIZES:
            cells["establisher"].takes.append(take_id)
        # An insert is a detail, which means nobody is the subject of it.
        if size in TIGHT_SIZES and take_id not in peopled:
            cells["insert"].takes.append(take_id)
        if clean_plate:
            cells["plate"].takes.append(take_id)

    return SceneRow(cells=cells)


def people_seen(client, scene_id: str) -> int:
    """The most people the Vision Agent saw in any one take of this scene.

    Being on camera and being identifiable are two different facts, and only
    the second one needs a face. A masked character, or a wide of two people
    running upstairs, is people nobody can name — and folding that into "no
    people" let a scene with two actors in it report itself fully covered.
    """
    row = client.query(
        f"SELECT max(length(subjects)) FROM {DB}.take_analysis "
        f"WHERE scene_id = %(s)s",
        parameters={"s": scene_id},
    ).result_rows
    return int(row[0][0]) if row and row[0][0] else 0


def summarise(rows: list[CharacterRow], scene: SceneRow | None = None,
              seen: int = 0) -> dict:
    """Totals the interface and the agents both use.

    `seen` is how many people are on camera. Left at zero the scene is taken
    at its word, which is right for a scene nobody has looked at yet.
    """
    missing = [
        {"character_id": r.character_id, "name": r.name, "band": c.band,
         "label": BAND_LABEL[c.band], "recover_cost_usd": c.recover_cost_usd}
        for r in rows for c in r.missing
    ]
    required = sum(1 for r in rows for c in r.cells.values() if c.required)
    have = sum(1 for r in rows for c in r.cells.values() if c.required and c.have)

    if scene is not None:
        missing += [
            {"character_id": SCENE_ROW, "name": "the scene", "band": c.band,
             "label": SCENE_LABEL[c.band], "recover_cost_usd": c.recover_cost_usd}
            for c in scene.missing
        ]
        required += sum(1 for c in scene.cells.values() if c.required)
        have += sum(1 for c in scene.cells.values() if c.required and c.have)

    # No people is not the same as no gaps. A scene nobody appears in, that
    # nobody has asked anything of, has nothing to be short of, and calling
    # that "covered" would let the crew walk away from a scene never checked.
    # People on camera that nobody could put a name to. Their coverage cannot
    # be computed, so the scene has not been checked — saying 100% here tells
    # the crew to walk away from a scene with actors still in it.
    unnamed = max(0, seen - len(rows))

    return {
        "characters": len(rows),
        "seen": seen,
        "unnamed": unnamed,
        "required": required,
        "have": have,
        "judged": required > 0 and not (not rows and unnamed),
        "completeness": round(have / required, 3) if required else 0.0,
        "missing": sorted(missing, key=lambda m: -m["recover_cost_usd"]),
        "exposure_usd": sum(m["recover_cost_usd"] for m in missing),
    }


def scenes_of(client, production_id: str) -> list[tuple[str, str]]:
    """Every scene in the day, in order, with where it is."""
    return [(r[0], r[1].replace("_", " ")) for r in client.query(
        f"SELECT scene_id, location_id FROM {DB}.scenes "
        f"WHERE production_id = %(p)s AND location_id != 'nothing_yet' "
        f"ORDER BY scene_id",
        parameters={"p": production_id},
    ).result_rows]


def day(client, production_id: str) -> tuple[list[CharacterRow], dict]:
    """Where the whole day stands, not the scene that happens to be open.

    The gate is asked at a company move, and a company move is a decision
    about the day. Summed rather than run over one flat list of people,
    because a person appears in several scenes and is short of different
    things in each, and because the scene-level shots only mean anything
    against the scene they belong to.
    """
    rows: list[CharacterRow] = []
    total = {"characters": 0, "required": 0, "have": 0, "missing": []}

    from core.crew import day_rate as rate_of
    rate = rate_of(production_id)

    for scene_id, place in scenes_of(client, production_id):
        here = matrix(client, scene_id, rate)
        summary = summarise(here, scene_shots(client, scene_id, rate),
                            seen=people_seen(client, scene_id))
        rows += here
        total["characters"] += summary["characters"]
        total["required"] += summary["required"]
        total["have"] += summary["have"]
        total["missing"] += [dict(m, scene_id=scene_id, place=place)
                             for m in summary["missing"]]

    required, have = total["required"], total["have"]
    return rows, {
        **total,
        "judged": required > 0,
        "completeness": round(have / required, 3) if required else 0.0,
        "missing": sorted(total["missing"], key=lambda m: -m["recover_cost_usd"]),
        "exposure_usd": sum(m["recover_cost_usd"] for m in total["missing"]),
    }


def scene_as_json(scene: SceneRow) -> list[dict]:
    return [
        {"shot": shot, "label": SCENE_LABEL[shot], "help": SCENE_HELP[shot],
         "required": scene.cells[shot].required,
         "state": scene.cells[shot].state,
         "takes": scene.cells[shot].takes,
         "recover_cost_usd": scene.cells[shot].recover_cost_usd}
        for shot in SCENE_SHOTS
    ]


def as_json(rows: list[CharacterRow]) -> list[dict]:
    return [
        {
            "character_id": r.character_id,
            "name": r.name,
            "face_uri": r.face_uri,
            "appearances": r.appearances,
            "complete": r.complete,
            "cells": [
                {"band": b, "label": BAND_LABEL[b], "help": BAND_HELP[b],
                 "required": r.cells[b].required, "state": r.cells[b].state,
                 "takes": r.cells[b].takes,
                 "recover_cost_usd": r.cells[b].recover_cost_usd}
                for b in BANDS
            ],
        }
        for r in rows
    ]
