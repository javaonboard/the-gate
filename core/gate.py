"""The gate, coverage and clock, together.

Coverage alone says what is missing. The simulator alone says whether the day
holds. Neither is actionable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from core import character_coverage as cc
from core.simulator import (
    PendingSetup,
    SimulationResult,
    pending_setups,
    simulate,
)
from core.union_rules import (STOPS_THE_DAY, DayAssessment, Person,
                              Violation, assess_day)


@dataclass
class Missing:
    """A shot this scene needs and does not have."""

    character_id: str
    person: str
    band: str
    label: str
    recover_cost_usd: int

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
                    recover_cost_usd=m["recover_cost_usd"])
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


def _recovery_setup(req: Missing, template: PendingSetup) -> PendingSetup:
    """A hypothetical setup to capture a missing requirement.

    Modelled on a setup already in this scene, so the duration distribution it
    draws from is the one this crew is actually working against today.
    """
    return replace(
        template,
        setup_id=f"recover_{req.character_id}_{req.band}",
        label=req.describe,
    )


def build_report(client, scene_id: str, now: datetime, call: datetime,
                 crew: list[Person], next_call: datetime,
                 completed_setup_ids: set[str] | None = None,
                 trials: int = 10_000, distant: bool = False) -> GateReport:
    rows = cc.matrix(client, scene_id)
    scene = cc.scene_shots(client, scene_id)
    coverage = Coverage(rows=rows, summary=cc.summarise(rows, scene))

    remaining = pending_setups(client, scene_id, completed_setup_ids)
    baseline = simulate(client, remaining, now=now, call=call, crew=crew,
                        next_call=next_call, trials=trials, distant=distant)

    options: list[RecoveryOption] = []
    template = remaining[0] if remaining else None

    if template is not None:
        # most expensive to recover first, that is the one worth the argument
        for req in sorted(coverage.missing(),
                          key=lambda m: -m.recover_cost_usd):
            with_recovery = remaining + [_recovery_setup(req, template)]
            after = simulate(client, with_recovery, now=now, call=call, crew=crew,
                             next_call=next_call, trials=trials, distant=distant)
            options.append(RecoveryOption(
                requirement=req,
                shoot_now_usd=after.expected_penalty_usd - baseline.expected_penalty_usd,
                recover_later_usd=req.recover_cost_usd,
                p_make_day_after=after.p_make_the_day,
            ))

    # The rules are assessed against the day this simulation actually expects,
    # so the verdict and the cost describe the same day.
    compliance = assess_day(call, baseline.median_wrap, crew,
                            next_call=next_call, distant=distant)

    return GateReport(scene_id=scene_id, now=now, coverage=coverage,
                      baseline=baseline, options=options,
                      compliance=compliance)


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
