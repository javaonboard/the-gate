"""A forked day has to arrive complete, and nothing was checking that.

`ws.fork` copies a fixed list of tables. A table added to the schema later is
not in that list, so a copy comes back missing it — with no error, because
every query still runs and simply finds nothing.

That is exactly what happened twice over. `production_world` was left out, so
a fork opened with its footage judged but its world blank; with no period to
be wrong against, nothing in the frames could be an anachronism. And
`take_problems` was left out, so every fault the QC pass had already found
vanished from the copy. The day looked analysed. It was not.

So this test does not name the tables. It reads the schema, finds everything
keyed by production, and demands each one be either forked or written down
here as deliberately shared.
"""

from __future__ import annotations

import re
from pathlib import Path

from api.workspace import FORKED

ROOT = Path(__file__).resolve().parents[1]

# Tables a workspace reads but must never own a copy of. Each is history or
# the outside world: shared on purpose, and forking it would either duplicate
# the studio's whole library per visitor or fork the weather.
SHARED = {
    # The studio's back catalogue — eighteen productions over four years. A
    # shoot day is not one of these; it is measured against them.
    "productions",
    # How long this crew's setups have taken, across every production. The
    # simulator samples it. A copy with its own private history would have
    # nothing to sample.
    "setup_duration_stats",
    # Per-second rows and frame embeddings behind the synthetic library.
    # Hundreds of millions of rows, none of them about the day in hand.
    "take_frames",
    "take_embeddings",
    # Rain, permits, road closures. The world does not fork.
    "world_events",
}


def production_keyed() -> set[str]:
    """Every table in the schema with a production_id column."""
    found = set()
    for sql in (ROOT / "data").glob("schema*.sql"):
        text = sql.read_text(encoding="utf-8")
        for match in re.finditer(
            r"CREATE TABLE IF NOT EXISTS the_gate\.(\w+)\s*\((.*?)\)\s*ENGINE",
            text, re.S,
        ):
            name, body = match.group(1), match.group(2)
            if re.search(r"^\s*production_id\b", body, re.M):
                found.add(name)
    return found


def test_the_schema_is_still_readable():
    assert len(production_keyed()) >= 8, (
        "the finder stopped finding tables, which would make every other "
        "assertion here pass for the wrong reason"
    )


def test_every_production_table_is_forked_or_deliberately_shared():
    unaccounted = production_keyed() - set(FORKED) - SHARED
    assert not unaccounted, (
        "these tables are keyed by production but a copy of a day never gets "
        f"them: {sorted(unaccounted)}.\n"
        "Add each to FORKED in api/workspace.py, or to SHARED here with the "
        "reason it is history rather than part of the day."
    )


def test_a_forked_table_copies_every_column_the_schema_has():
    """A column added later is silently dropped from the copy.

    The fork names its columns, so a new one is not picked up: the row still
    inserts, the value is just the type's default. A world with its notes
    silently emptied still reads as a world.
    """
    thin = []
    for sql in (ROOT / "data").glob("schema*.sql"):
        text = sql.read_text(encoding="utf-8")
        for match in re.finditer(
            r"CREATE TABLE IF NOT EXISTS the_gate\.(\w+)\s*\((.*?)\)\s*ENGINE",
            text, re.S,
        ):
            name, body = match.group(1), match.group(2)
            if name not in FORKED:
                continue
            columns = [
                line for line in re.findall(r"^\s{4}(\w+)\s+\S", body, re.M)
                # An index is not a column, and a MATERIALIZED one is computed
                # by ClickHouse on insert — naming either would break the copy
                # rather than complete it.
                if line not in ("INDEX", "PRIMARY", "CONSTRAINT")
            ]
            computed = set(re.findall(r"^\s{4}(\w+)\s.*\b(?:MATERIALIZED|ALIAS)\b",
                                      body, re.M))
            missing = [c for c in columns
                       if c not in FORKED[name] and c not in computed]
            if missing:
                thin.append(f"{name} does not copy {missing}")
    assert not thin, "\n".join(thin)
