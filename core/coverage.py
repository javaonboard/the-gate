"""Coverage matrix and the gate decision.

Answers the question the 1st AD actually asks: can we move on?

Deliberately not an LLM. Whether a scene is covered is a matching problem
between what an editor needs and what is on the card — it has a correct answer,
and a correct answer should be computed, not generated. The agent explains this
decision; it does not make it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable

import clickhouse_connect
from dotenv import load_dotenv

load_dotenv()

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")

WIDE = {"ELS", "LS", "MLS"}
MID = {"MS"}
TIGHT = {"MCU", "CU", "ECU"}

# A take is only usable if it is technically acceptable. These are the floors.
MIN_FOCUS = 0.55
MIN_EXPOSURE = 0.45

BLOCKING_FAULTS = {"boom visible", "boom in frame", "soft focus", "out of focus"}


@dataclass
class Take:
    take_id: str
    setup_id: str
    shot_size: str
    movement: str
    subjects: list[str]
    subjects_count: int
    screen_direction: str
    focus_score: float
    exposure_score: float
    faults: list[str]

    @property
    def usable(self) -> bool:
        if self.focus_score < MIN_FOCUS or self.exposure_score < MIN_EXPOSURE:
            return False
        return not any(f.lower() in BLOCKING_FAULTS for f in self.faults)

    @property
    def band(self) -> str:
        if self.shot_size in WIDE:
            return "wide"
        if self.shot_size in TIGHT:
            return "tight"
        return "mid"


@dataclass
class Requirement:
    req_id: str
    shot_type: str
    subject: str
    priority: int
    recover_cost_usd: int
    is_vfx_plate: bool

    satisfied_by: list[str] = field(default_factory=list)
    unusable_candidates: list[str] = field(default_factory=list)

    @property
    def satisfied(self) -> bool:
        return bool(self.satisfied_by)


@dataclass
class GateDecision:
    scene_id: str
    requirements: list[Requirement]
    takes: list[Take]

    @property
    def missing(self) -> list[Requirement]:
        return [r for r in self.requirements if not r.satisfied]

    @property
    def blocking(self) -> list[Requirement]:
        """Priority 1 gaps — the scene cannot be cut without these."""
        return [r for r in self.missing if r.priority == 1]

    @property
    def go(self) -> bool:
        return not self.blocking

    @property
    def completeness(self) -> float:
        if not self.requirements:
            return 1.0
        return len([r for r in self.requirements if r.satisfied]) / len(self.requirements)

    @property
    def exposure_usd(self) -> int:
        """What it would cost to recover everything still missing."""
        return sum(r.recover_cost_usd for r in self.missing)

    def ranked_missing(self) -> list[Requirement]:
        """Most expensive to fix later, first."""
        return sorted(
            self.missing,
            key=lambda r: (r.priority, -r.recover_cost_usd),
        )


def _matches(req: Requirement, take: Take, cast_size: int) -> bool:
    """Does this take satisfy this requirement?

    Matching is by framing and how many people are in shot, because that is what
    an editor is actually looking for. Subject identity is deliberately loose —
    the Vision Agent describes people rather than naming them.
    """
    if req.is_vfx_plate:
        return take.subjects_count == 0 and take.band == "wide"

    if req.shot_type == "master":
        return take.band == "wide" and take.subjects_count >= max(2, cast_size - 1)

    if req.shot_type == "establishing":
        return take.band == "wide"

    if req.shot_type == "single":
        return take.band == "tight" and take.subjects_count == 1

    if req.shot_type == "ots":
        return take.band in {"tight", "mid"} and take.subjects_count == 2

    if req.shot_type in {"insert", "reaction"}:
        return take.band == "tight" and take.subjects_count <= 1

    return False


def _assign(requirements: list[Requirement], takes: list[Take], cast_size: int) -> None:
    """Match takes to requirements, one setup satisfying one requirement.

    Requirements of the same shot type compete for distinct setups — three
    singles need three different camera positions, not three takes of one.
    """
    claimed: set[str] = set()

    for req in sorted(requirements, key=lambda r: (r.priority, -r.recover_cost_usd)):
        candidates = [t for t in takes if _matches(req, t, cast_size)]
        if not candidates:
            continue

        free = [t for t in candidates if t.setup_id not in claimed]
        usable = [t for t in free if t.usable]

        if usable:
            best = max(usable, key=lambda t: t.focus_score)
            req.satisfied_by = [best.take_id]
            claimed.add(best.setup_id)
        elif free:
            req.unusable_candidates = [t.take_id for t in free]


def evaluate(client, scene_id: str) -> GateDecision:
    reqs = [
        Requirement(req_id=r[0], shot_type=r[1], subject=r[2], priority=r[3],
                    recover_cost_usd=r[4], is_vfx_plate=bool(r[5]))
        for r in client.query(
            f"SELECT req_id, shot_type, subject, priority, recover_cost_usd, "
            f"is_vfx_plate FROM {DB}.scene_requirements WHERE scene_id = %(s)s",
            parameters={"s": scene_id},
        ).result_rows
    ]

    takes = [
        Take(take_id=r[0], setup_id=r[1], shot_size=r[2], movement=r[3],
             subjects=list(r[4]), subjects_count=len(r[4]), screen_direction=r[5],
             focus_score=float(r[6]), exposure_score=float(r[7]), faults=list(r[8]))
        for r in client.query(
            f"SELECT take_id, setup_id, shot_size, movement, subjects, "
            f"screen_direction, focus_score, exposure_score, continuity_flags "
            f"FROM {DB}.take_analysis WHERE scene_id = %(s)s",
            parameters={"s": scene_id},
        ).result_rows
    ]

    cast_size = 1
    rows = client.query(
        f"SELECT length(characters) FROM {DB}.scenes WHERE scene_id = %(s)s",
        parameters={"s": scene_id},
    ).result_rows
    if rows:
        cast_size = max(1, rows[0][0])

    _assign(reqs, takes, cast_size)
    return GateDecision(scene_id=scene_id, requirements=reqs, takes=takes)


def connect():
    return clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        port=int(os.environ.get("CLICKHOUSE_PORT", 8443)),
        username=os.environ["CLICKHOUSE_USER"],
        password=os.environ["CLICKHOUSE_PASSWORD"],
        secure=True,
        database=DB,
    )


def render(decision: GateDecision) -> str:
    lines = []
    verdict = "GO" if decision.go else "NO-GO"
    lines.append(f"{verdict}   {decision.scene_id}")
    lines.append(f"coverage {decision.completeness:.0%}   "
                 f"{len(decision.takes)} takes   "
                 f"exposure ${decision.exposure_usd:,}")
    lines.append("")

    for req in sorted(decision.requirements, key=lambda r: (r.priority, r.req_id)):
        mark = "ok  " if req.satisfied else ("MISS" if req.priority == 1 else "gap ")
        detail = req.satisfied_by[0] if req.satisfied else f"${req.recover_cost_usd:,} to recover"
        subj = f" {req.subject}" if req.subject != "-" else ""
        lines.append(f"  {mark}  p{req.priority}  {req.shot_type:12s}{subj:12s} {detail}")
        if req.unusable_candidates:
            lines.append(f"          candidates exist but are unusable: "
                         f"{', '.join(req.unusable_candidates)}")

    if decision.blocking:
        lines.append("")
        lines.append("blocking:")
        for req in decision.ranked_missing():
            if req.priority == 1:
                lines.append(f"  {req.shot_type} {req.subject} "
                             f"— ${req.recover_cost_usd:,} to pick up later")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    scene = sys.argv[1] if len(sys.argv) > 1 else "prod_now_sc001"
    print(render(evaluate(connect(), scene)))
