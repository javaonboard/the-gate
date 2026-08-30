"""Day Simulator, will we make the day?

Monte Carlo over the setups still to shoot. Each trial draws a duration for
every remaining setup from what this crew has actually done before, adds them
up, and asks the union rule engine what the resulting wrap time costs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from dotenv import load_dotenv

from core.union_rules import (
    OT_1_5X_AFTER_HOURS,
    Person,
    assess_day,
    latest_wrap_without_violation,
)

load_dotenv()

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")

QUANTILES = [0.1, 0.25, 0.5, 0.75, 0.9]

# Below this many observed setups a bucket is too thin to trust, and we widen
# the conditioning rather than quote a number built on a handful of days.
MIN_SAMPLES = 150

DEFAULT_TRIALS = 10_000
SEED = 20260816


@dataclass
class PendingSetup:
    setup_id: str
    scene_id: str
    scene_type: str
    int_ext: str
    day_night: str
    extras_bucket: int
    dp_id: str
    label: str = ""


@dataclass
class Distribution:
    quantiles: list[float]      # seconds, at QUANTILES
    samples: int
    conditioned_on: str

    @property
    def median_minutes(self) -> float:
        return self.quantiles[2] / 60.0


@dataclass
class SimulationResult:
    trials: int
    call: datetime
    hard_stop: datetime
    wrap_times: np.ndarray
    penalty_costs: np.ndarray
    setups: list[PendingSetup]
    distributions: dict[str, Distribution] = field(default_factory=dict)

    @property
    def p_make_the_day(self) -> float:
        # Compare in epoch seconds, wrap_times were built from naive local
        # datetimes, so a datetime64 comparison would apply a UTC offset.
        hard = np.int64(self.hard_stop.timestamp())
        as_int = self.wrap_times.astype("datetime64[s]").astype("int64")
        return float((as_int <= hard).mean())

    @property
    def median_wrap(self) -> datetime:
        return self._pct(50)

    @property
    def p90_wrap(self) -> datetime:
        return self._pct(90)

    def _pct(self, p: float) -> datetime:
        # Epochs were built from naive local datetimes, so read them back the
        # same way. Mixing in utcfromtimestamp shifts everything by the local
        # UTC offset.
        as_int = self.wrap_times.astype("datetime64[s]").astype("int64")
        return datetime.fromtimestamp(float(np.percentile(as_int, p)))

    @property
    def expected_penalty_usd(self) -> float:
        return float(self.penalty_costs.mean())

    @property
    def p90_penalty_usd(self) -> float:
        return float(np.percentile(self.penalty_costs, 90))


def _fetch(client, where: str, params: dict) -> tuple[list[float], int]:
    row = client.query(
        f"SELECT quantilesTDigestMerge({', '.join(str(q) for q in QUANTILES)})(durations), "
        f"countMerge(n) FROM {DB}.setup_duration_stats WHERE {where}",
        parameters=params,
    ).result_rows
    if not row or not row[0][1]:
        return [], 0
    return list(row[0][0]), int(row[0][1])


def duration_distribution(client, setup: PendingSetup) -> Distribution:
    """Get a duration distribution, widening the conditioning if it is too thin.

    A dawn interior with 80 extras shot by one DP may have been done six times.
    Quoting a distribution off six observations produces confident nonsense, so
    each fallback drops the least important condition.
    """
    ladder = [
        ("dp_id = %(dp)s AND int_ext = %(ie)s AND day_night = %(dn)s "
         "AND scene_type = %(st)s AND extras_bucket = %(eb)s",
         "dp+int/ext+day/night+type+extras"),
        ("dp_id = %(dp)s AND int_ext = %(ie)s AND day_night = %(dn)s "
         "AND scene_type = %(st)s",
         "dp+int/ext+day/night+type"),
        ("dp_id = %(dp)s AND int_ext = %(ie)s AND day_night = %(dn)s",
         "dp+int/ext+day/night"),
        ("int_ext = %(ie)s AND day_night = %(dn)s", "int/ext+day/night"),
        ("1", "everything"),
    ]
    params = {
        "dp": setup.dp_id, "ie": setup.int_ext, "dn": setup.day_night,
        "st": setup.scene_type, "eb": setup.extras_bucket,
    }

    for where, label in ladder:
        quantiles, samples = _fetch(client, where, params)
        if samples >= MIN_SAMPLES and quantiles:
            return Distribution(quantiles, samples, label)

    quantiles, samples = _fetch(client, "1", params)
    return Distribution(quantiles or [1800.0] * 5, samples, "fallback")


def _sample(rng: np.random.Generator, dist: Distribution, size: int) -> np.ndarray:
    """Draw durations by interpolating the empirical quantile curve.

    Sampling the real curve rather than fitting a named distribution keeps the
    long right tail that makes shoot days overrun.
    """
    u = rng.random(size)
    return np.interp(u, QUANTILES, dist.quantiles)


def simulate(client, setups: list[PendingSetup], now: datetime, call: datetime,
             crew: list[Person], next_call: datetime,
             turnover_minutes: float = 8.0, trials: int = DEFAULT_TRIALS,
             distant: bool = False, seed: int = SEED) -> SimulationResult:
    rng = np.random.default_rng(seed)

    dists = {s.setup_id: duration_distribution(client, s) for s in setups}

    remaining = np.zeros(trials)
    for s in setups:
        remaining += _sample(rng, dists[s.setup_id], trials)
        remaining += turnover_minutes * 60.0

    wrap_epoch = np.int64(now.timestamp()) + remaining.astype("int64")
    wrap_times = wrap_epoch.astype("datetime64[s]")

    hard_stop = latest_wrap_without_violation(call, crew, next_call, distant)

    # Cost each trial through the real rule engine. Assessing every trial is
    # wasteful, so quantise wrap times to five-minute buckets and reuse.
    costs = np.zeros(trials)
    cache: dict[int, float] = {}
    buckets = (wrap_epoch // 300).astype("int64")
    meal = [call + timedelta(hours=6, minutes=30)]

    for i, bucket in enumerate(buckets):
        cost = cache.get(int(bucket))
        if cost is None:
            wrap = datetime.fromtimestamp(int(bucket) * 300)
            assessment = assess_day(call, wrap, crew, meal_breaks=meal,
                                    next_call=next_call, distant=distant)
            cost = assessment.total_cost_usd
            cache[int(bucket)] = cost
        costs[i] = cost

    return SimulationResult(
        trials=trials, call=call, hard_stop=hard_stop, wrap_times=wrap_times,
        penalty_costs=costs, setups=setups, distributions=dists,
    )


def pending_setups(client, scene_id: str, completed_setup_ids: set[str] | None = None
                   ) -> list[PendingSetup]:
    """Setups still to shoot for a scene."""
    done = completed_setup_ids or set()
    rows = client.query(
        f"SELECT setup_id, scene_id, scene_type, int_ext, day_night, "
        f"extras_bucket, dp_id FROM {DB}.setups WHERE scene_id = %(s)s",
        parameters={"s": scene_id},
    ).result_rows
    return [
        PendingSetup(setup_id=r[0], scene_id=r[1], scene_type=r[2], int_ext=r[3],
                     day_night=r[4], extras_bucket=int(r[5]), dp_id=r[6])
        for r in rows if r[0] not in done
    ]


def render(result: SimulationResult) -> str:
    p = result.p_make_the_day
    lines = [
        f"{p:.0%} chance of making the day"
        f"   ({result.trials:,} trials)",
        f"hard stop {result.hard_stop:%H:%M}"
        f"   median wrap {result.median_wrap:%H:%M}"
        f"   p90 {result.p90_wrap:%H:%M}",
        f"expected penalty ${result.expected_penalty_usd:,.0f}"
        f"   p90 ${result.p90_penalty_usd:,.0f}",
        "",
        f"{len(result.setups)} setups remaining:",
    ]
    for s in result.setups:
        d = result.distributions[s.setup_id]
        lines.append(
            f"  {s.setup_id:24s} {d.median_minutes:5.0f} min median"
            f"   n={d.samples:<6d} on {d.conditioned_on}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    from core.coverage import connect

    scene = sys.argv[1] if len(sys.argv) > 1 else "prod_now_sc001"
    client = connect()

    all_setups = pending_setups(client, scene)
    if not all_setups:
        raise SystemExit(f"no setups for {scene}")

    # It is mid-afternoon: the first two thirds are shot, the rest are not.
    done = {s.setup_id for s in all_setups[: (len(all_setups) * 2) // 3]}
    remaining = [s for s in all_setups if s.setup_id not in done]

    call = datetime(2026, 8, 16, 7, 0)
    now = call + timedelta(hours=float(sys.argv[2]) if len(sys.argv) > 2 else 9)
    next_call = call + timedelta(days=1, hours=1)

    crew = (
        [Person(f"camera_{i:02d}", "camera") for i in range(18)]
        + [Person(f"cast_{i}", "cast", kind="performer", hourly_rate=180.0)
           for i in range(3)]
    )

    result = simulate(client, remaining, now=now, call=call, crew=crew,
                      next_call=next_call)

    print(f"scene {scene}   now {now:%H:%M}   "
          f"{len(done)} setups shot, {len(remaining)} to go\n")
    print(render(result))
