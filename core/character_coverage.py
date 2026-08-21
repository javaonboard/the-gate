"""Coverage, per person.

The gate answers "can we move on" for a scene. This answers the question the AD
actually asks out loud: *what have we got on each of them, and what is missing?*

Four bands, because that is how coverage is discussed on a floor:

    wide     you can see where they are
    medium   waist up, the workhorse
    close    the face, for the lines and the reactions
    over     over-the-shoulder, how a conversation is cut

Which takes satisfy which band is computed, not judged. A take counts for a
character only if that character is actually in it, in the foreground.
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
    "close": "close-up",
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
DEFAULT_COST = {"wide": 20000, "medium": 15000, "close": 30000, "over": 18000}

# Someone glimpsed once in the background is not a character.
MIN_APPEARANCES = 2


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


def matrix(client, scene_id: str) -> list[CharacterRow]:
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

    # take -> shot size, and how many foreground people were in it
    takes = {
        r[0]: {"size": r[1], "people": r[2]}
        for r in client.query(
            f"""
            SELECT a.take_id, a.shot_size,
                   (SELECT count() FROM {DB}.take_characters AS tc
                    WHERE tc.take_id = a.take_id AND tc.prominence = 'foreground')
            FROM {DB}.take_analysis AS a
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

    rows: list[CharacterRow] = []
    for character_id, name, face_uri, count in cast:
        if count < MIN_APPEARANCES:
            continue

        cells: dict[str, Cell] = {}
        for band in BANDS:
            required, cost = overrides.get(
                (character_id, band),
                (DEFAULT_REQUIRED[band], DEFAULT_COST[band]),
            )
            cells[band] = Cell(band=band, required=required, recover_cost_usd=cost)

        for take_id in by_character.get(character_id, []):
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
            appearances=count, cells=cells,
        ))

    return rows


def summarise(rows: list[CharacterRow]) -> dict:
    """Totals the interface and the agents both use."""
    missing = [
        {"character_id": r.character_id, "name": r.name, "band": c.band,
         "label": BAND_LABEL[c.band], "recover_cost_usd": c.recover_cost_usd}
        for r in rows for c in r.missing
    ]
    required = sum(1 for r in rows for c in r.cells.values() if c.required)
    have = sum(1 for r in rows for c in r.cells.values() if c.required and c.have)
    return {
        "characters": len(rows),
        "required": required,
        "have": have,
        "completeness": round(have / required, 3) if required else 1.0,
        "missing": sorted(missing, key=lambda m: -m["recover_cost_usd"]),
        "exposure_usd": sum(m["recover_cost_usd"] for m in missing),
    }


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
