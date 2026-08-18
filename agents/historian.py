"""The Book — production records.

Every studio has one person who has been there long enough to say "that will
take you until seven." This is that, built from eighteen productions and four
years of setups.

It reaches ClickHouse two ways. Typed tools answer the questions we know get
asked, quickly and in a shape the rest of the system can use. The MCP connection
is there for everything else — when someone asks a question nobody anticipated,
the agent writes the SQL itself against the live cluster.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from dotenv import load_dotenv

from core.coverage import connect

load_dotenv()

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")
QUANTILES = "0.1, 0.25, 0.5, 0.75, 0.9"

_local = threading.local()


def client():
    """One client per thread — the tools get called from a thread pool."""
    existing = getattr(_local, "client", None)
    if existing is None:
        existing = connect()
        _local.client = existing
    return existing


# --- tools ------------------------------------------------------------------

def how_long_does_this_take(dp_id: str, interior_exterior: str, time_of_day: str,
                            scene_type: str = "", extras: int = 0) -> dict[str, Any]:
    """How long this DP has historically taken on this kind of setup.

    Returns the spread, not an average — the tail is what makes days overrun.
    If we have too few observations to be honest about, it widens the question
    and says so.

    Args:
        dp_id: Which DP, e.g. "dp_lind".
        interior_exterior: "INT" or "EXT".
        time_of_day: "DAY", "NIGHT", "DUSK" or "DAWN".
        scene_type: "dialogue", "action", "stunt", "vfx" or "montage". Optional.
        extras: Roughly how many background performers.
    """
    bucket = 0 if extras == 0 else 1 if extras <= 5 else 2 if extras <= 20 else 3 if extras <= 50 else 4

    ladder = [
        ("dp_id = %(dp)s AND int_ext = %(ie)s AND day_night = %(dn)s "
         "AND scene_type = %(st)s AND extras_bucket = %(eb)s",
         "this DP, these exact conditions"),
        ("dp_id = %(dp)s AND int_ext = %(ie)s AND day_night = %(dn)s "
         "AND scene_type = %(st)s",
         "this DP, this kind of scene"),
        ("dp_id = %(dp)s AND int_ext = %(ie)s AND day_night = %(dn)s",
         "this DP, interior/exterior and time of day"),
        ("int_ext = %(ie)s AND day_night = %(dn)s", "any DP, similar conditions"),
    ]
    params = {"dp": dp_id, "ie": interior_exterior, "dn": time_of_day,
              "st": scene_type or "dialogue", "eb": bucket}

    for where, basis in ladder:
        rows = client().query(
            f"SELECT quantilesTDigestMerge({QUANTILES})(durations), countMerge(n) "
            f"FROM {DB}.setup_duration_stats WHERE {where}",
            parameters=params,
        ).result_rows
        if rows and rows[0][1] >= 150:
            q = [round(v / 60) for v in rows[0][0]]
            return {
                "based_on": basis,
                "setups_observed": int(rows[0][1]),
                "fastest_10_percent_minutes": q[0],
                "typical_minutes": q[2],
                "slow_day_minutes": q[4],
                "confident": True,
            }

    return {"based_on": "not enough history", "setups_observed": 0,
            "typical_minutes": 45, "confident": False}


def compare_dps(interior_exterior: str, time_of_day: str) -> list[dict[str, Any]]:
    """Every DP side by side under the same conditions.

    Args:
        interior_exterior: "INT" or "EXT".
        time_of_day: "DAY", "NIGHT", "DUSK" or "DAWN".
    """
    rows = client().query(
        f"""
        SELECT dp_id, countMerge(n) AS setups,
               round(quantilesTDigestMerge(0.5)(durations)[1] / 60) AS typical,
               round(quantilesTDigestMerge(0.9)(durations)[1] / 60) AS slow
        FROM {DB}.setup_duration_stats
        WHERE int_ext = %(ie)s AND day_night = %(dn)s
        GROUP BY dp_id ORDER BY typical
        """,
        parameters={"ie": interior_exterior, "dn": time_of_day},
    ).result_rows
    return [
        {"dp_id": r[0], "setups_observed": r[1],
         "typical_minutes": r[2], "slow_day_minutes": r[3]}
        for r in rows
    ]


def how_much_coverage_do_we_usually_get(scene_type: str = "dialogue") -> dict[str, Any]:
    """Typical number of setups and takes for this kind of scene.

    Useful for sanity-checking whether today is unusual or normal.

    Args:
        scene_type: "dialogue", "action", "stunt", "vfx" or "montage".
    """
    rows = client().query(
        f"""
        SELECT round(avg(setups), 1), round(avg(takes), 1), count()
        FROM (
            SELECT s.scene_id AS scene_id,
                   uniq(s.setup_id) AS setups,
                   count(t.take_id) AS takes
            FROM {DB}.setups AS s
            LEFT JOIN {DB}.takes AS t USING (setup_id)
            WHERE s.scene_type = %(st)s
            GROUP BY s.scene_id
        )
        """,
        parameters={"st": scene_type},
    ).result_rows
    if not rows or not rows[0][2]:
        return {"scene_type": scene_type, "scenes_observed": 0}
    return {
        "scene_type": scene_type,
        "typical_setups": rows[0][0],
        "typical_takes": rows[0][1],
        "scenes_observed": rows[0][2],
    }


def what_was_happening_then(location_id: str, at_time: str) -> list[dict[str, Any]]:
    """What the world was doing at a moment, nearest event before it.

    Uses ClickHouse ASOF JOIN, which matches on the closest earlier timestamp
    rather than an exact one — the right tool for lining shooting up against
    weather and closures.

    Args:
        location_id: The location, e.g. "canal_street".
        at_time: Timestamp, "YYYY-MM-DD HH:MM:SS".
    """
    rows = client().query(
        f"""
        SELECT e.ts, e.kind, e.severity, e.summary, e.citation_url
        FROM (SELECT %(l)s AS location_id, toDateTime(%(t)s) AS ts) AS q
        ASOF LEFT JOIN {DB}.world_events AS e
          ON e.location_id = q.location_id AND e.ts <= q.ts
        """,
        parameters={"l": location_id, "t": at_time},
    ).result_rows
    return [
        {"ts": str(r[0]), "kind": r[1], "severity": r[2],
         "summary": r[3], "source": r[4]}
        for r in rows if r[0]
    ]


TOOLS = [
    how_long_does_this_take,
    compare_dps,
    how_much_coverage_do_we_usually_get,
    what_was_happening_then,
]


# --- the agent --------------------------------------------------------------

INSTRUCTION = """You keep the production records for a studio. You have watched
eighteen productions over four years — every setup, every take, who was shooting
and how long it took.

Answer from the records, never from impression. Use the tools.

How to answer:
- Give the typical time and the slow-day time, not a single number. A shoot day
  is ruined by the tail, not the average.
- Always say how many setups the answer is based on. If it is thin, say so
  plainly rather than sounding confident.
- Refer to people by name, not by id — dp_lind is Lind.
- Keep it to a couple of sentences unless asked for the breakdown.

If a question needs data the typed tools do not cover, query the cluster
directly through the ClickHouse tools and explain what you looked at."""


def build_agent(callbacks: dict | None = None, with_mcp: bool = True):
    """The Book as an ADK agent.

    with_mcp adds the live ClickHouse MCP connection so the agent can write its
    own SQL for questions the typed tools do not answer. It needs the MCP server
    running:  $env:CLICKHOUSE_MCP_SERVER_TRANSPORT="http"; mcp-clickhouse
    """
    from google.adk.agents import Agent

    tools = list(TOOLS)

    if with_mcp and os.environ.get("CLICKHOUSE_MCP_URL"):
        from google.adk.tools.mcp_tool import McpToolset
        from google.adk.tools.mcp_tool.mcp_session_manager import (
            StreamableHTTPConnectionParams,
        )

        tools.append(
            McpToolset(
                connection_params=StreamableHTTPConnectionParams(
                    url=os.environ["CLICKHOUSE_MCP_URL"],
                    headers={
                        "Authorization":
                            f"Bearer {os.environ['CLICKHOUSE_MCP_AUTH_TOKEN']}"
                    },
                )
            )
        )

    return Agent(
        model=os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash"),
        name="historian",
        description="Remembers how long this crew has taken on past shoots.",
        instruction=INSTRUCTION,
        tools=tools,
        **(callbacks or {}),
    )


if __name__ == "__main__":
    import json

    print("Lind, exterior night:")
    print(json.dumps(
        how_long_does_this_take("dp_lind", "EXT", "NIGHT", "dialogue"), indent=2))

    print("\nEvery DP, exterior night:")
    for row in compare_dps("EXT", "NIGHT"):
        print(f"  {row['dp_id']:16s} {row['typical_minutes']:3.0f} min typical"
              f"   {row['slow_day_minutes']:3.0f} min slow"
              f"   ({row['setups_observed']} setups)")

    print("\nDialogue scenes usually run:")
    print(json.dumps(how_much_coverage_do_we_usually_get("dialogue"), indent=2))
