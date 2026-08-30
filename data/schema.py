"""Create the tables.

Five .sql files, applied in order. Safe to run again, every statement is
CREATE ... IF NOT EXISTS, so this is how you bring a fresh ClickHouse up and
also how you add whatever is new after a pull.
    """

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from core.coverage import connect

load_dotenv()

HERE = Path(__file__).resolve().parent

# In order: the tables everything else refers to, then the ones that hang off
# them, then the views the agent is allowed to read.
FILES = [
    "schema.sql",
    "schema_casting.sql",
    "schema_qc.sql",
    "schema_world.sql",
    "marts.sql",
]


def statements(sql: str) -> list[str]:
    """Split a file into statements, ignoring comments and blank lines."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true",
                    help="say what would run without running it")
    args = ap.parse_args()

    ch = None if args.list else connect()

    for name in FILES:
        path = HERE / name
        if not path.exists():
            print(f"  {name}: not here, skipped")
            continue

        parts = statements(path.read_text(encoding="utf-8"))
        if args.list:
            print(f"  {name}: {len(parts)} statements")
            continue

        done = 0
        for statement in parts:
            try:
                ch.command(statement)
                done += 1
            except Exception as exc:
                # One bad statement should not stop the rest, a table that
                # already exists in a different shape is worth seeing, not
                # worth halting on.
                print(f"  {name}: {type(exc).__name__}: "
                      f"{str(exc).splitlines()[0][:90]}")
        print(f"  {name}: {done}/{len(parts)} applied")


if __name__ == "__main__":
    main()
