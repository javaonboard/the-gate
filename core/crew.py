"""Who is on the clock.

Every figure this product produces is a headcount multiplied by a rate: a meal
penalty is paid to each person, overtime is each person's hours, an invaded
turnaround is double time for everyone it touches.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime

from core.coverage import connect
from core.union_rules import Person

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")

# What each department is called on a call sheet, in the order it appears.
DEPARTMENTS = [
    ("production", "Production"),
    ("ad", "Assistant Directors"),
    ("camera", "Camera"),
    ("grip_electric", "Grip and Electric"),
    ("sound", "Sound"),
    ("art", "Art Department"),
    ("wardrobe_hmu", "Wardrobe, Hair and Make-up"),
    ("support", "Locations, Catering, Drivers"),
]


@dataclass
class Tier:
    key: str
    label: str
    blurb: str
    departments: dict[str, int]
    cast: int
    crew_rate: float
    cast_rate: float

    @property
    def crew_size(self) -> int:
        return sum(self.departments.values())


TIERS = [
    Tier(
        key="micro",
        label="Two people and a camera",
        blurb="A creator shoot. Everyone does three jobs.",
        departments={"production": 1, "ad": 0, "camera": 1, "grip_electric": 0,
                     "sound": 1, "art": 0, "wardrobe_hmu": 0, "support": 0},
        cast=2, crew_rate=35.0, cast_rate=45.0,
    ),
    Tier(
        key="indie",
        label="Small independent",
        blurb="Departments exist, one person deep. A short or a low-budget feature.",
        departments={"production": 3, "ad": 2, "camera": 3, "grip_electric": 3,
                     "sound": 2, "art": 2, "wardrobe_hmu": 2, "support": 1},
        cast=4, crew_rate=45.0, cast_rate=95.0,
    ),
    Tier(
        key="standard",
        label="Standard production",
        blurb="A drama, a series, a funded feature. Real department heads.",
        departments={"production": 6, "ad": 4, "camera": 7, "grip_electric": 10,
                     "sound": 3, "art": 8, "wardrobe_hmu": 5, "support": 6},
        cast=8, crew_rate=52.0, cast_rate=180.0,
    ),
    Tier(
        key="studio",
        label="Studio feature",
        blurb="Every department fully staffed, plus the trucks that carry them.",
        departments={"production": 14, "ad": 8, "camera": 16, "grip_electric": 32,
                     "sound": 5, "art": 26, "wardrobe_hmu": 14, "support": 25},
        cast=15, crew_rate=64.0, cast_rate=340.0,
    ),
]

BY_KEY = {t.key: t for t in TIERS}
DEFAULT_TIER = "standard"


def as_people(departments: dict[str, int], cast: int, minors: int,
              crew_rate: float, cast_rate: float) -> list[Person]:
    """The crew, as the rule engine wants it.

    Departments are kept as the person's department so a violation can say who
    it lands on, "grip and electric, eleven people" is actionable in a way
    that "eleven people" is not.
    """
    people: list[Person] = []
    for key, _label in DEPARTMENTS:
        for i in range(max(0, int(departments.get(key, 0)))):
            people.append(Person(f"{key}_{i:02d}", key, hourly_rate=crew_rate))

    for i in range(max(0, int(cast))):
        people.append(Person(f"cast_{i:02d}", "cast", kind="performer",
                             hourly_rate=cast_rate, is_minor=i < max(0, minors)))
    return people


def from_tier(key: str, minors: int = 0) -> list[Person]:
    tier = BY_KEY.get(key, BY_KEY[DEFAULT_TIER])
    return as_people(tier.departments, tier.cast, minors,
                     tier.crew_rate, tier.cast_rate)


def tier_json() -> list[dict]:
    return [
        {"key": t.key, "label": t.label, "blurb": t.blurb,
         "departments": t.departments, "cast": t.cast,
         "crew_size": t.crew_size,
         "crew_rate": t.crew_rate, "cast_rate": t.cast_rate}
        for t in TIERS
    ]


# --- what this production actually recorded ---------------------------------

_client = None


def _ch():
    """One connection, opened when first needed."""
    global _client
    if _client is None:
        _client = connect()
    return _client


def crew_table() -> None:
    _ch().command(f"""
        CREATE TABLE IF NOT EXISTS {DB}.crew_plan
        (
            production_id String,
            tier          LowCardinality(String),
            departments   String,
            cast          UInt16,
            minors        UInt16,
            crew_rate     Float32,
            cast_rate     Float32,
            updated_at    DateTime
        )
        ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (production_id)
    """)


def crew_of(production_id: str) -> dict:
    """Who is on the clock for this day, and what they cost.

    Falls back to the standard tier rather than to nothing: a day with no crew
    would report every penalty as zero, which is a confident wrong answer.
    """
    crew_table()
    rows = _ch().query(
        f"SELECT tier, departments, cast, minors, crew_rate, cast_rate "
        f"FROM {DB}.crew_plan FINAL WHERE production_id = %(p)s",
        parameters={"p": production_id},
    ).result_rows
    if not rows:
        tier = BY_KEY[DEFAULT_TIER]
        return {"tier": tier.key, "departments": dict(tier.departments),
                "cast": tier.cast, "minors": 0,
                "crew_rate": tier.crew_rate, "cast_rate": tier.cast_rate,
                "set_by_hand": False}

    tier, departments, cast, minors, crew_rate, cast_rate = rows[0]
    return {"tier": tier, "departments": json.loads(departments),
            "cast": cast, "minors": minors,
            "crew_rate": float(crew_rate), "cast_rate": float(cast_rate),
            "set_by_hand": True}


# A pickup is priced as a share of a day, and this is the day. Ten hours is a
# standard call — the point at which overtime has started but double time has
# not — so it is what a production quotes a day at.
PICKUP_HOURS = 10.0


def day_rate(production_id: str) -> float:
    """What one more day with this unit costs in wages.

    Everything about recovering a shot scales with this. A two-person crew and
    a studio unit were both quoted thirty thousand to come back for the same
    close-up, which made the crew size decorative for the number the AD is
    actually looking at.
    """
    return sum(p.hourly_rate for p in people_of(production_id)) * PICKUP_HOURS


def people_of(production_id: str) -> list[Person]:
    """The crew, ready for the rule engine."""
    plan = crew_of(production_id)
    return as_people(plan["departments"], plan["cast"], plan["minors"],
                     plan["crew_rate"], plan["cast_rate"])
