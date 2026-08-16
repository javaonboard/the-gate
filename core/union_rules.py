"""Union rule engine — turnaround, meal penalties and overtime.

Deterministic. Given a call time, a projected wrap and who is on the crew, this
returns exactly which rules break and what that costs. The simulator uses it as
a constraint; the gate uses it to price the choice between grabbing another
setup and going into penalty.

Figures below are from the 2024 IATSE Basic Agreement and the 2026 SAG-AFTRA
TV/Theatrical Agreement. Where a number is an approximation it is marked, and
those should be confirmed against the current rate sheets before anyone relies
on the dollar figures for real scheduling.

Sources:
  iatse.net/wp-content/uploads/2024/07/2024-IATSE-Basic-Agreement-MOA-FINAL.pdf
  sagaftra.org/overtime, sagaftra.org/meal-periods
  greenslate.com/blog/official-iatse-basic-agreement-changes-and-effective-dates
  wrapbook.com/blog/meal-penalties-producers-guide
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# --- verified ---------------------------------------------------------------

# IATSE: meal must begin by the sixth hour, and every six hours thereafter.
MEAL_DUE_AFTER_HOURS = 6.0
MEAL_DURATION_MIN = 30

# IATSE daily rest between wrap and next call: 10 hours at studio, 9 distant.
TURNAROUND_STUDIO_HOURS = 10.0
TURNAROUND_DISTANT_HOURS = 9.0

# SAG-AFTRA: performers get 12 hours.
TURNAROUND_PERFORMER_HOURS = 12.0

# IATSE weekly rest.
WEEKLY_TURNAROUND_5_DAY_HOURS = 54.0
WEEKLY_TURNAROUND_6_DAY_HOURS = 32.0

# Crew overtime multipliers by elapsed hours worked.
OT_1_5X_AFTER_HOURS = 8.0
OT_2X_AFTER_HOURS = 12.0

# SAG-AFTRA performers: 1.5x for hours 9 and 10, 2x beyond 10.
PERFORMER_OT_1_5X_AFTER_HOURS = 8.0
PERFORMER_OT_2X_AFTER_HOURS = 10.0

# SAG-AFTRA meal penalty, principal performers, from the fifth half-hour.
# Effective 1 July 2026. Flat thereafter.
PERFORMER_MEAL_PENALTY_5TH_PLUS = 75.0
BACKGROUND_MEAL_PENALTY_5TH_PLUS = 15.0

# IATSE: invading the rest period is now paid at double time for the hours
# invaded, having previously been additional straight time.
TURNAROUND_INVASION_MULTIPLIER = 2.0

# --- approximations — confirm against current rate sheets --------------------

# SAG-AFTRA meal penalties escalate over the first four half-hours before
# settling at the verified $75. These tiers are estimates.
PERFORMER_MEAL_PENALTY_TIERS = [25.0, 35.0, 50.0, 60.0]  # APPROXIMATE

# IATSE meal penalties are paid as one hour of the individual's prevailing rate
# per half-hour of delay, so they scale with the rate rather than being flat.
IATSE_MEAL_PENALTY_HOURS_PER_HALF_HOUR = 1.0

# Representative IATSE theatrical hourly rate, used when a crew member has no
# rate on file.
DEFAULT_CREW_HOURLY_RATE = 52.0


@dataclass
class Person:
    person_id: str
    department: str
    kind: str = "crew"          # crew | performer | background
    hourly_rate: float = DEFAULT_CREW_HOURLY_RATE
    is_minor: bool = False


@dataclass
class Violation:
    rule: str
    severity: str               # warning | violation
    detail: str
    cost_usd: float = 0.0
    people: int = 0


@dataclass
class DayAssessment:
    call: datetime
    wrap: datetime
    violations: list[Violation] = field(default_factory=list)

    @property
    def elapsed_hours(self) -> float:
        return (self.wrap - self.call).total_seconds() / 3600.0

    @property
    def total_cost_usd(self) -> float:
        return sum(v.cost_usd for v in self.violations)

    @property
    def has_violation(self) -> bool:
        return any(v.severity == "violation" for v in self.violations)


def _rate_at(elapsed: float, person: Person) -> float:
    """Prevailing hourly rate at a given point in the day."""
    if person.kind == "performer":
        if elapsed > PERFORMER_OT_2X_AFTER_HOURS:
            return person.hourly_rate * 2.0
        if elapsed > PERFORMER_OT_1_5X_AFTER_HOURS:
            return person.hourly_rate * 1.5
        return person.hourly_rate
    if elapsed > OT_2X_AFTER_HOURS:
        return person.hourly_rate * 2.0
    if elapsed > OT_1_5X_AFTER_HOURS:
        return person.hourly_rate * 1.5
    return person.hourly_rate


def overtime_cost(call: datetime, wrap: datetime, crew: list[Person]) -> float:
    """Cost of the hours worked beyond straight time."""
    elapsed = (wrap - call).total_seconds() / 3600.0
    total = 0.0
    for p in crew:
        first = PERFORMER_OT_1_5X_AFTER_HOURS if p.kind == "performer" else OT_1_5X_AFTER_HOURS
        second = PERFORMER_OT_2X_AFTER_HOURS if p.kind == "performer" else OT_2X_AFTER_HOURS
        if elapsed <= first:
            continue
        hours_1_5 = max(0.0, min(elapsed, second) - first)
        hours_2 = max(0.0, elapsed - second)
        total += hours_1_5 * p.hourly_rate * 0.5      # the premium above straight
        total += hours_2 * p.hourly_rate * 1.0
    return total


def meal_penalty(call: datetime, wrap: datetime, meal_breaks: list[datetime],
                 crew: list[Person]) -> list[Violation]:
    """Penalty for meals taken late, or not taken at all."""
    violations: list[Violation] = []
    due = call + timedelta(hours=MEAL_DUE_AFTER_HOURS)
    taken = sorted(meal_breaks)
    actual = taken[0] if taken else wrap

    if actual <= due:
        return violations

    late_minutes = (actual - due).total_seconds() / 60.0
    half_hours = max(1, math.ceil(late_minutes / 30.0))
    elapsed_at_meal = (actual - call).total_seconds() / 3600.0

    total = 0.0
    for p in crew:
        if p.kind == "performer":
            for i in range(half_hours):
                if i < len(PERFORMER_MEAL_PENALTY_TIERS):
                    total += PERFORMER_MEAL_PENALTY_TIERS[i]
                else:
                    total += PERFORMER_MEAL_PENALTY_5TH_PLUS
        elif p.kind == "background":
            total += half_hours * BACKGROUND_MEAL_PENALTY_5TH_PLUS
        else:
            # IATSE: one hour at the prevailing rate per half-hour of delay
            total += half_hours * IATSE_MEAL_PENALTY_HOURS_PER_HALF_HOUR * _rate_at(
                elapsed_at_meal, p
            )

    violations.append(Violation(
        rule="meal_penalty",
        severity="violation",
        detail=f"first meal {int(late_minutes)} min late "
               f"({half_hours} half-hour increment{'s' if half_hours > 1 else ''})",
        cost_usd=round(total, 2),
        people=len(crew),
    ))
    return violations


def turnaround(wrap: datetime, next_call: datetime, crew: list[Person],
               distant: bool = False) -> list[Violation]:
    """Rest between wrap and the next day's call."""
    violations: list[Violation] = []
    gap = (next_call - wrap).total_seconds() / 3600.0
    crew_required = TURNAROUND_DISTANT_HOURS if distant else TURNAROUND_STUDIO_HOURS

    for kind, required in (("crew", crew_required),
                           ("performer", TURNAROUND_PERFORMER_HOURS)):
        affected = [p for p in crew if p.kind == kind]
        if not affected or gap >= required:
            continue
        invaded = required - gap
        cost = sum(
            invaded * p.hourly_rate * TURNAROUND_INVASION_MULTIPLIER for p in affected
        )
        violations.append(Violation(
            rule=f"turnaround_{kind}",
            severity="violation",
            detail=f"{gap:.1f}h rest against {required:.0f}h required — "
                   f"{invaded:.1f}h invaded",
            cost_usd=round(cost, 2),
            people=len(affected),
        ))
    return violations


def minors(call: datetime, wrap: datetime, crew: list[Person]) -> list[Violation]:
    """Minors are capped well below an adult day."""
    kids = [p for p in crew if p.is_minor]
    if not kids:
        return []
    elapsed = (wrap - call).total_seconds() / 3600.0
    if elapsed <= 9.5:
        return []
    return [Violation(
        rule="minor_hours",
        severity="violation",
        detail=f"{elapsed:.1f}h on set against a 9.5h cap for minors",
        people=len(kids),
    )]


def assess_day(call: datetime, wrap: datetime, crew: list[Person],
               meal_breaks: list[datetime] | None = None,
               next_call: datetime | None = None,
               distant: bool = False) -> DayAssessment:
    """Everything that breaks, and what it costs."""
    assessment = DayAssessment(call=call, wrap=wrap)
    elapsed = assessment.elapsed_hours

    assessment.violations += meal_penalty(call, wrap, meal_breaks or [], crew)
    assessment.violations += minors(call, wrap, crew)

    if next_call is not None:
        assessment.violations += turnaround(wrap, next_call, crew, distant)

    ot = overtime_cost(call, wrap, crew)
    if ot > 0:
        assessment.violations.append(Violation(
            rule="overtime",
            severity="warning",
            detail=f"{elapsed:.1f}h day — "
                   f"{max(0.0, elapsed - OT_1_5X_AFTER_HOURS):.1f}h beyond straight time",
            cost_usd=round(ot, 2),
            people=len(crew),
        ))

    return assessment


def latest_wrap_without_violation(call: datetime, crew: list[Person],
                                  next_call: datetime,
                                  distant: bool = False) -> datetime:
    """The hard stop — wrapping later than this invades someone's rest."""
    required = max(
        TURNAROUND_DISTANT_HOURS if distant else TURNAROUND_STUDIO_HOURS,
        TURNAROUND_PERFORMER_HOURS if any(p.kind == "performer" for p in crew) else 0.0,
    )
    return next_call - timedelta(hours=required)


if __name__ == "__main__":
    # A 14-hour day, first meal an hour and a quarter late, next call 22h out.
    call = datetime(2026, 8, 16, 7, 0)
    wrap = call + timedelta(hours=14)
    next_call = call + timedelta(hours=22)

    crew = (
        [Person(f"camera_{i:02d}", "camera") for i in range(18)]
        + [Person(f"cast_{i}", "cast", kind="performer", hourly_rate=180.0)
           for i in range(3)]
        + [Person(f"bg_{i:02d}", "background", kind="background", hourly_rate=25.0)
           for i in range(12)]
    )

    result = assess_day(
        call, wrap, crew,
        meal_breaks=[call + timedelta(hours=7, minutes=15)],
        next_call=next_call,
    )

    print(f"call {call:%H:%M}   wrap {wrap:%H:%M}   "
          f"{result.elapsed_hours:.1f}h   {len(crew)} people")
    print(f"latest clean wrap: "
          f"{latest_wrap_without_violation(call, crew, next_call):%H:%M}")
    print()
    for v in result.violations:
        print(f"  {v.rule:22s} {v.severity:9s} ${v.cost_usd:>10,.0f}   {v.detail}")
    print(f"\n  {'TOTAL':22s} {'':9s} ${result.total_cost_usd:>10,.0f}")
