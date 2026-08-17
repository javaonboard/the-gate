"""The gate — coverage and clock, together.

Coverage alone says what is missing. The simulator alone says whether the day
holds. Neither is actionable. This puts them side by side and prices the choice:
what does it cost to grab a missing shot now, against what it costs to come back
for it later?

That comparison is the product. Everything else feeds it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from core.coverage import GateDecision, Requirement, evaluate
from core.simulator import (
    PendingSetup,
    SimulationResult,
    pending_setups,
    simulate,
)
from core.union_rules import Person


@dataclass
class RecoveryOption:
    """One missing requirement, priced both ways."""

    requirement: Requirement
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
class GateReport:
    scene_id: str
    now: datetime
    coverage: GateDecision
    baseline: SimulationResult
    options: list[RecoveryOption] = field(default_factory=list)

    @property
    def go(self) -> bool:
        return self.coverage.go

    @property
    def verdict(self) -> str:
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
        if self.go:
            return (f"Covered. {p:.0%} chance of making the day, "
                    f"hard stop {self.baseline.hard_stop:%H:%M}.")

        rec = self.recommended
        if not rec:
            blocking = ", ".join(f"{r.shot_type} {r.subject}".strip()
                                 for r in self.coverage.blocking)
            return (f"Missing {blocking}, but staying costs more than the pickup. "
                    f"Wrap and schedule it.")

        first = rec[0]
        others = len(rec) - 1
        tail = f" Plus {others} more worth grabbing." if others else ""
        return (
            f"{p:.0%} chance of making the day. Missing "
            f"{first.requirement.shot_type} {first.requirement.subject}".strip()
            + f" — {first.verdict}. Odds drop to "
              f"{first.p_make_day_after:.0%} if you shoot it.{tail}"
        )


def _recovery_setup(req: Requirement, template: PendingSetup) -> PendingSetup:
    """A hypothetical setup to capture a missing requirement.

    Modelled on a setup already in this scene, so the duration distribution it
    draws from is the one this crew is actually working against today.
    """
    return replace(
        template,
        setup_id=f"recover_{req.req_id}",
        label=f"{req.shot_type} {req.subject}".strip(),
    )


def build_report(client, scene_id: str, now: datetime, call: datetime,
                 crew: list[Person], next_call: datetime,
                 completed_setup_ids: set[str] | None = None,
                 trials: int = 10_000, distant: bool = False) -> GateReport:
    coverage = evaluate(client, scene_id)

    remaining = pending_setups(client, scene_id, completed_setup_ids)
    baseline = simulate(client, remaining, now=now, call=call, crew=crew,
                        next_call=next_call, trials=trials, distant=distant)

    options: list[RecoveryOption] = []
    template = remaining[0] if remaining else None

    if template is not None:
        for req in coverage.ranked_missing():
            with_recovery = remaining + [_recovery_setup(req, template)]
            after = simulate(client, with_recovery, now=now, call=call, crew=crew,
                             next_call=next_call, trials=trials, distant=distant)
            options.append(RecoveryOption(
                requirement=req,
                shoot_now_usd=after.expected_penalty_usd - baseline.expected_penalty_usd,
                recover_later_usd=req.recover_cost_usd,
                p_make_day_after=after.p_make_the_day,
            ))

    return GateReport(scene_id=scene_id, now=now, coverage=coverage,
                      baseline=baseline, options=options)


def render(report: GateReport) -> str:
    b = report.baseline
    lines = [
        f"{report.verdict}   {report.scene_id}   {report.now:%H:%M}",
        "",
        f"coverage        {report.coverage.completeness:.0%}"
        f"   ({len(report.coverage.takes)} takes)",
        f"make the day    {b.p_make_the_day:.0%}"
        f"   hard stop {b.hard_stop:%H:%M}"
        f"   median wrap {b.median_wrap:%H:%M}",
        f"penalty so far  ${b.expected_penalty_usd:,.0f} expected",
        "",
    ]

    if report.options:
        lines.append("missing, priced both ways:")
        for o in sorted(report.options, key=lambda o: -o.saving_usd):
            name = f"{o.requirement.shot_type} {o.requirement.subject}".strip()
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
