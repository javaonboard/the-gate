"""The gate, coverage and clock, together.

Coverage alone says what is missing. The simulator alone says whether the day
holds. Neither is actionable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from core import character_coverage as cc
from core.simulator import (
    duration_distribution,
    PendingSetup,
    SimulationResult,
    day_setups,
    pending_setups,
    simulate,
)
from core.union_rules import (STOPS_THE_DAY, DayAssessment, Person,
                              Violation, assess_day)


@dataclass
class Missing:
    """A shot a scene needs and does not have."""

    character_id: str
    person: str
    band: str
    label: str
    recover_cost_usd: int
    scene_id: str = ""
    place: str = ""

    @property
    def describe(self) -> str:
        return f"{self.label} of {self.person}"


@dataclass
class RecoveryOption:
    """One missing shot, priced both ways."""

    requirement: Missing
    shoot_now_usd: float          # extra penalty cost from staying longer
    recover_later_usd: int        # cost of a pickup
    p_make_day_after: float       # odds of making the day if we shoot it

    # How long it would take, so it can be said whether it fits.
    minutes: float = 0.0

    @property
    def saving_usd(self) -> float:
        return self.recover_later_usd - self.shoot_now_usd

    @property
    def worth_it(self) -> bool:
        return self.saving_usd > 0

    @property
    def verdict(self) -> str:
        if self.worth_it:
            return (f"shoot now — ${self.shoot_now_usd:,.0f} in overtime "
                    f"against ${self.recover_later_usd:,} to pick up later")
        return (f"let it go — ${self.shoot_now_usd:,.0f} tonight is more than "
                f"the ${self.recover_later_usd:,} pickup")


@dataclass
class Coverage:
    """What the scene has, per person. The same numbers the table shows."""

    rows: list
    summary: dict

    @property
    def judged(self) -> bool:
        """Whether there was anything here to have an opinion about."""
        return bool(self.summary.get("judged"))

    @property
    def go(self) -> bool:
        # Nothing to be short of is not the same as nothing missing.
        return self.judged and not self.summary["missing"]

    @property
    def completeness(self) -> float:
        return self.summary["completeness"]

    @property
    def exposure_usd(self) -> int:
        return self.summary["exposure_usd"]

    @property
    def people(self) -> int:
        return self.summary["characters"]

    def missing(self) -> list[Missing]:
        return [
            Missing(character_id=m["character_id"], person=m["name"],
                    band=m["band"], label=m["label"],
                    recover_cost_usd=m["recover_cost_usd"],
                    scene_id=m.get("scene_id", ""),
                    place=m.get("place", ""))
            for m in self.summary["missing"]
        ]


@dataclass
class GateReport:
    scene_id: str
    now: datetime
    coverage: Coverage
    baseline: SimulationResult
    options: list[RecoveryOption] = field(default_factory=list)
    compliance: DayAssessment | None = None
    # The same day, if they go and get everything worth getting.
    recovery: SimulationResult | None = None

    @property
    def blocked_by_rule(self) -> list[Violation]:
        """Rules that stop the day rather than cost money.

Overtime, a late meal and an invaded turnaround are expensive, not
        forbidden, a production chooses to pay them, and saying NO-GO on cost
        would be inventing a rule that does not exist.
        """
        if self.compliance is None:
            return []
        return [v for v in self.compliance.violations
                if v.rule in STOPS_THE_DAY]

    @property
    def go(self) -> bool:
        # A GO is never returned without the rule check having run. It is a
        # statement that the company can move, and that cannot be said from
        # coverage alone.
        if self.compliance is None:
            return False
        return self.coverage.go and not self.blocked_by_rule

    @property
    def verdict(self) -> str:
        if self.compliance is None:
            return "NOT CHECKED"
        if not self.coverage.judged:
            return "NOT CHECKED"
        return "GO" if self.go else "NO-GO"

    @property
    def room_for(self) -> int:
        """How many of the shots worth grabbing actually fit before rest.

        Counted in order of what they save, because that is the order anyone
        would shoot them in. Arithmetic on the median, not another ten thousand
        trials: the question is how much fits in the time left, and a spread
        around it does not help anyone standing on the floor.
        """
        if self.recovery is None:
            return 0
        left = (self.baseline.hard_stop - self.now).total_seconds()
        if left <= 0:
            return 0

        fits = 0
        for option in self.recommended:
            left -= option.minutes * 60
            if left < 0:
                break
            fits += 1
        return fits

    @property
    def minutes_left(self) -> float:
        """Until the crew are owed their rest."""
        return max(0.0, (self.baseline.hard_stop - self.now).total_seconds() / 60)

    @property
    def recommended(self) -> list[RecoveryOption]:
        """Worth shooting now, most valuable first."""
        return sorted(
            [o for o in self.options if o.worth_it],
            key=lambda o: -o.saving_usd,
        )

    @property
    def total_saving_usd(self) -> float:
        return sum(o.saving_usd for o in self.recommended)

    def summary(self) -> str:
        """The one sentence an AD needs."""
        p = self.baseline.p_make_the_day

        if not self.coverage.judged:
            takes = len(getattr(self.coverage, "rows", []))
            return (
                "Nobody has been found on camera in this scene yet, so there is "
                "nothing to check coverage against. If people should be in it, "
                "the footage may not have been looked at — or they may not be "
                "recognisable in it."
            )

        if self.go:
            return (f"Everyone's covered. {p:.0%} chance of making the day, "
                    f"hard stop {self.baseline.hard_stop:%H:%M}.")

        gaps = self.coverage.missing()
        rec = self.recommended

        if not rec:
            names = ", ".join(m.describe for m in gaps[:2])
            return (f"Short a {names}, but staying costs more than the pickup. "
                    f"Wrap and schedule it.")

        first = rec[0]
        others = len(gaps) - 1
        tail = (f" {others} more still short." if others > 0 else "")
        return (
            f"{p:.0%} chance of making the day. Short a "
            f"{first.requirement.describe} — {first.verdict}. "
            f"Odds drop to {first.p_make_day_after:.0%} if you shoot it.{tail}"
        )


# What framing each missing shot would be taken at. The band is what an editor
# asks for; the shot size is what it costs to light.
BAND_FRAMING = {"wide": "LS", "medium": "MS", "close": "CU", "over": "MCU"}

# A scene's own shots, when nobody is in them.
SCENE_FRAMING = {"establisher": "ELS", "insert": "ECU", "plate": "LS"}


def _recovery_setup(req: Missing, template: PendingSetup,
                    already_lit: set[str] | None = None) -> PendingSetup:
    """A hypothetical setup to capture a missing requirement.

    Modelled on a setup already in this scene, so the duration distribution it
    draws from is the one this crew is actually working against today — and at
    the framing the missing shot would need, because that is most of what a
    camera position costs.

    If the crew already has a position at that framing, this is not a new
    setup at all. They are lit for it; somebody stands in the right place and
    they go again. Pricing that as a fresh hour was why every missing shot came
    back costing the same to grab, whatever it was.
    """
    framing = BAND_FRAMING.get(req.band) or SCENE_FRAMING.get(req.band, "MS")
    setup = replace(
        template,
        setup_id=f"recover_{req.character_id}_{req.band}",
        label=req.describe,
        shot_size=framing,
    )
    if already_lit and framing in already_lit:
        # Another take on a position that is up. The lighting, the blocking and
        # the camera are all already there, so what it costs is a take, not a
        # setup — and the simulator draws it from the takes this crew has
        # actually shot rather than from their setups.
        setup = replace(setup, retake=True)
    return setup


def _any_setup(client, production_id: str) -> PendingSetup | None:
    """One setup from today, for the conditions this crew is working in."""
    rows = client.query(
        f"SELECT setup_id, scene_id, scene_type, int_ext, day_night, "
        f"extras_bucket, dp_id, shot_size FROM {cc.DB}.setups "
        f"WHERE production_id = %(p)s ORDER BY setup_id LIMIT 1",
        parameters={"p": production_id},
    ).result_rows
    if not rows:
        return None
    r = rows[0]
    return PendingSetup(setup_id=r[0], scene_id=r[1], scene_type=r[2],
                        int_ext=r[3], day_night=r[4], extras_bucket=int(r[5]),
                        dp_id=r[6], shot_size=r[7] or "")


def production_of(client, scene_id: str) -> str:
    """Which day's work a scene belongs to."""
    row = client.query(
        f"SELECT production_id FROM {cc.DB}.scenes WHERE scene_id = %(s)s LIMIT 1",
        parameters={"s": scene_id},
    ).result_rows
    return row[0][0] if row else "prod_now"


def build_report(client, scene_id: str, now: datetime, call: datetime,
                 crew: list[Person], next_call: datetime,
                 completed_setup_ids: set[str] | None = None,
                 trials: int = 10_000, distant: bool = False) -> GateReport:
    # The gate is asked at a company move, and a company move is a decision
    # about the day. Coverage and the simulation both cover every scene, so
    # "54% of the day" and "worth grabbing before we move" describe the same
    # day the verdict is about. scene_id only says which scene was open.
    production_id = production_of(client, scene_id)
    rows, summary = cc.day(client, production_id)
    coverage = Coverage(rows=rows, summary=summary)

    remaining = day_setups(client, production_id, completed_setup_ids)
    baseline = simulate(client, remaining, now=now, call=call, crew=crew,
                        next_call=next_call, trials=trials, distant=distant)

    options: list[RecoveryOption] = []

    # Something to model a recovery on: a setup still to shoot if there is
    # one, otherwise a position this crew has already worked today. A day
    # rebuilt from a camera card has nothing left on the schedule — everything
    # on it was shot — and the work left is the coverage that is missing. That
    # still has to be priced, so the conditions come from a setup they did.
    template = remaining[0] if remaining else _any_setup(client, production_id)

    # Which framings the crew is lit for, in each scene.
    #
    # Per scene, because lighting does not travel. Being set up for a close-up
    # in the hallway is worth nothing in the stairwell — that is a company
    # move and a relight. Asked across the whole day this counted every
    # missing shot as a pickup and priced them all the same again.
    lit_in: dict[str, set[str]] = {}
    for pending in day_setups(client, production_id):
        if pending.shot_size:
            lit_in.setdefault(pending.scene_id, set()).add(pending.shot_size)

    if template is not None:
        # most expensive to recover first, that is the one worth the argument
        for req in sorted(coverage.missing(),
                          key=lambda m: -m.recover_cost_usd):
            with_recovery = remaining + [
                _recovery_setup(req, template, lit_in.get(req.scene_id, set()))]
            after = simulate(client, with_recovery, now=now, call=call, crew=crew,
                             next_call=next_call, trials=trials, distant=distant)
            options.append(RecoveryOption(
                requirement=req,
                shoot_now_usd=after.expected_penalty_usd - baseline.expected_penalty_usd,
                recover_later_usd=req.recover_cost_usd,
                p_make_day_after=after.p_make_the_day,
                minutes=duration_distribution(
                    client, _recovery_setup(
                        req, template,
                        lit_in.get(req.scene_id, set()))).median_minutes,
            ))

    # And the day if they go and get what they are short.
    #
    # The baseline answers "will we finish what is still on the schedule",
    # which on a day rebuilt from a camera card is nothing — it was all shot.
    # That came out as a hundred per cent while the gate was saying ten shots
    # missing, which reads as a contradiction and is useless besides. Nobody
    # is deciding whether to finish an already finished plan. They are
    # deciding whether to go and get the coverage before the crew are owed
    # their rest, and that is what this simulates.
    worth_getting = [o for o in options if o.worth_it]
    if worth_getting:
        recovery = simulate(
            client,
            remaining + [_recovery_setup(o.requirement, template,
                                         lit_in.get(o.requirement.scene_id, set()))
                         for o in worth_getting],
            now=now, call=call, crew=crew, next_call=next_call,
            trials=trials, distant=distant)
    else:
        recovery = baseline

    # The rules are assessed against the day this simulation actually expects,
    # so the verdict and the cost describe the same day.
    compliance = assess_day(call, baseline.median_wrap, crew,
                            next_call=next_call, distant=distant)

    return GateReport(scene_id=scene_id, now=now, coverage=coverage,
                      baseline=baseline, options=options,
                      compliance=compliance, recovery=recovery)


def render(report: GateReport) -> str:
    b = report.baseline
    lines = [
        f"{report.verdict}   {report.scene_id}   {report.now:%H:%M}",
        "",
        f"coverage        {report.coverage.completeness:.0%}"
        f"   ({report.coverage.people} people)",
        f"make the day    {b.p_make_the_day:.0%}"
        f"   hard stop {b.hard_stop:%H:%M}"
        f"   median wrap {b.median_wrap:%H:%M}",
        f"penalty so far  ${b.expected_penalty_usd:,.0f} expected",
        "",
    ]

    if report.options:
        lines.append("missing, priced both ways:")
        for o in sorted(report.options, key=lambda o: -o.saving_usd):
            name = o.requirement.describe
            mark = "+" if o.worth_it else "-"
            lines.append(
                f"  {mark} {name:22s} now ${o.shoot_now_usd:>8,.0f}"
                f"   later ${o.recover_later_usd:>8,}"
                f"   save ${o.saving_usd:>8,.0f}"
                f"   day {o.p_make_day_after:.0%}"
            )
        lines.append("")

    lines.append(report.summary())
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    from datetime import timedelta

    from core.coverage import connect

    scene = sys.argv[1] if len(sys.argv) > 1 else "prod_now_sc001"
    hours_in = float(sys.argv[2]) if len(sys.argv) > 2 else 9.0

    client = connect()
    all_setups = pending_setups(client, scene)
    done = {s.setup_id for s in all_setups[: (len(all_setups) * 2) // 3]}

    call = datetime(2026, 8, 16, 7, 0)
    crew = (
        [Person(f"camera_{i:02d}", "camera") for i in range(18)]
        + [Person(f"cast_{i}", "cast", kind="performer", hourly_rate=180.0)
           for i in range(3)]
    )

    report = build_report(
        client, scene,
        now=call + timedelta(hours=hours_in),
        call=call,
        crew=crew,
        next_call=call + timedelta(days=1, hours=1),
        completed_setup_ids=done,
    )
    print(render(report))
