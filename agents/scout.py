"""Scout — the Location Scout.

Watches the world outside the fence. A shoot day is not disturbed by anything
inside the schedule; it is disturbed by a pulled permit, a street closure, an
unexpected event two roads away, or rain arriving at four.

None of that lives in a database. It lives on the open web, it changes without
warning, and nobody thinks to ask about it until it has already cost a day.

Three ways of working, in increasing weight:
  search   — a quick fact, right now
  research — a cited, structured briefing on a location and date
  watch    — a standing subscription; Parallel pushes to us when things change

The last one is the important one. It is what stops this being a thing you have
to remember to ask.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from typing import Any

from dotenv import load_dotenv
from parallel import Parallel

load_dotenv()

# Standing subscriptions we want for any location we are shooting at.
# Each becomes a Parallel monitor that webhooks us when something changes.
WATCHLIST = [
    {
        "kind": "permit",
        "frequency": "1d",
        "query": "Film permit changes, suspensions or revocations affecting "
                 "{location} in {city}. Include street closure notices issued "
                 "by the city.",
    },
    {
        "kind": "road_closure",
        "frequency": "1d",
        "query": "Road closures, roadworks or traffic restrictions on or around "
                 "{location} in {city} in the next seven days.",
    },
    {
        "kind": "local_event",
        "frequency": "1d",
        "query": "Public events, markets, demonstrations or construction near "
                 "{location} in {city} that would create crowds or noise.",
    },
    {
        "kind": "union_bulletin",
        "frequency": "1w",
        "query": "New IATSE or SAG-AFTRA bulletins, rate changes or working "
                 "condition updates affecting film production crews.",
    },
]

# What a briefing must come back with. Parallel returns citations per field,
# which is what makes this usable for a decision that costs money.
BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "sunset_local_time": {
            "type": "string",
            "description": "Local sunset time on the date, HH:MM, or empty if unknown",
        },
        "weather_outlook": {
            "type": "string",
            "description": "Short plain description of expected conditions",
        },
        "rain_risk": {
            "type": "string",
            "enum": ["none", "low", "moderate", "high", "unknown"],
        },
        "permit_status": {
            "type": "string",
            "description": "Anything found about filming permits or restrictions",
        },
        "road_closures": {
            "type": "array",
            "items": {"type": "string"},
        },
        "local_events": {
            "type": "array",
            "items": {"type": "string"},
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Things that could disrupt a film shoot that day",
        },
    },
    "required": ["weather_outlook", "rain_risk", "risks"],
}


@dataclass
class Briefing:
    location: str
    on_date: str
    content: dict[str, Any]
    citations: list[dict[str, Any]]
    run_id: str

    @property
    def rain_risk(self) -> str:
        return self.content.get("rain_risk", "unknown")

    @property
    def risks(self) -> list[str]:
        return self.content.get("risks", [])

    def summary(self) -> str:
        weather = self.content.get("weather_outlook", "no outlook")
        n = len(self.risks)
        if not n:
            return f"{weather}. Nothing flagged at {self.location}."
        return f"{weather}. {n} thing{'s' if n > 1 else ''} to watch at {self.location}."


def _client() -> Parallel:
    return Parallel(api_key=os.environ["PARALLEL_API_KEY"])


# --- tools ------------------------------------------------------------------

def search_web(objective: str, query: str) -> dict[str, Any]:
    """Look something up on the live web, right now.

    Use for a single fact that may have changed recently — a rate, a rule, a
    closure. Returns short excerpts with their source URLs.

    Args:
        objective: What you are trying to find out, in a sentence.
        query: The search phrase.
    """
    result = _client().search(objective=objective, search_queries=[query])
    return {
        "results": [
            {
                "title": r.title,
                "url": r.url,
                "excerpt": (r.excerpts[0][:600] if r.excerpts else ""),
            }
            for r in result.results[:6]
        ],
        "search_id": result.search_id,
    }


def research_location(location: str, city: str, on_date: str,
                      processor: str = "base") -> dict[str, Any]:
    """Build a cited briefing on a location for a shooting date.

    Covers sunset, weather, permits, road closures and local events. Every field
    comes back with its sources, so the call can be checked rather than trusted.

    Args:
        location: Where we are shooting, e.g. "canal street".
        city: The city, e.g. "Amsterdam".
        on_date: ISO date of the shoot day.
        processor: Parallel tier. "base" is enough for this; "core" digs deeper.
    """
    client = _client()
    task = client.task_run.create(
        input=(
            f"Film production shooting at {location} in {city} on {on_date}. "
            f"Find the local sunset time, the weather outlook, the status of "
            f"filming permits, any road closures, and any public events nearby. "
            f"Focus on anything that could disrupt an outdoor film shoot."
        ),
        task_spec={"output_schema": BRIEFING_SCHEMA},
        processor=processor,
    )
    result = client.task_run.result(task.run_id, api_timeout=600)

    content = result.output.content
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            content = {"weather_outlook": content, "rain_risk": "unknown", "risks": []}

    basis = getattr(result.output, "basis", []) or []
    citations = []
    for item in basis:
        for cite in (getattr(item, "citations", None) or []):
            citations.append({
                "field": getattr(item, "field", ""),
                "url": getattr(cite, "url", ""),
                "excerpt": (getattr(cite, "excerpts", None) or [""])[0][:300],
            })

    return {
        "location": location,
        "date": on_date,
        "briefing": content,
        "citations": citations,
        "run_id": task.run_id,
    }


def watch_location(location: str, city: str, webhook_url: str,
                   kinds: list[str] | None = None) -> dict[str, Any]:
    """Subscribe to changes at a location.

    Creates standing Parallel monitors. From then on Parallel pushes to our
    webhook when a permit, closure, event or union bulletin changes — we do not
    poll and we do not have to remember to look.

    Args:
        location: Where we are shooting.
        city: The city.
        webhook_url: Public URL that will receive monitor events.
        kinds: Which watches to set up. Defaults to all of them.
    """
    client = _client()
    wanted = set(kinds) if kinds else {w["kind"] for w in WATCHLIST}
    created = []

    for watch in WATCHLIST:
        if watch["kind"] not in wanted:
            continue
        monitor = client.monitor.create(
            type="event_stream",
            frequency=watch["frequency"],
            processor="lite",
            settings={"query": watch["query"].format(location=location, city=city)},
            webhook={"url": webhook_url,
                     "event_types": ["monitor.event.detected"]},
        )
        created.append({
            "kind": watch["kind"],
            "monitor_id": getattr(monitor, "monitor_id", None) or getattr(monitor, "id", ""),
            "frequency": watch["frequency"],
        })

    return {"location": location, "monitors": created}


TOOLS = [search_web, research_location, watch_location]


# --- the agent --------------------------------------------------------------

INSTRUCTION = """You are the location scout on a film production that is
shooting today.

Your job is to know what is happening outside the set that could cost the
production time or money, and to say it plainly.

How to work:
- Use research_location for a full briefing on the shooting location and date.
- Use search_web for a single fact you need to confirm right now.
- Use watch_location to set up standing watches on a location we will return to.

When you report:
- Lead with anything that changes what the crew should do in the next few hours.
- Say how confident you are, and cite where it came from.
- Weather matters because of light and because exteriors slow down. Closures and
  events matter because they move the company or add noise. Permit changes matter
  because they can stop the day entirely.
- If nothing is wrong, say so in one line. Do not pad.

Never guess a fact you could look up. Never present a forecast as certainty."""


def build_agent(callbacks: dict | None = None):
    """The Scout as an ADK agent. Imported lazily so the tools stay usable alone."""
    from google.adk.agents import Agent

    return Agent(
        model=os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash"),
        name="scout",
        description="Watches weather, permits, closures and events at the location.",
        instruction=INSTRUCTION,
        tools=TOOLS,
        **(callbacks or {}),
    )


if __name__ == "__main__":
    import sys

    where = sys.argv[1] if len(sys.argv) > 1 else "canal street, Amsterdam"
    location, _, city = where.partition(",")
    city = city.strip() or "Amsterdam"
    when = sys.argv[2] if len(sys.argv) > 2 else str(date.today())

    print(f"researching {location.strip()} in {city} on {when}…\n")
    out = research_location(location.strip(), city, when)

    briefing = out["briefing"]
    for key, value in briefing.items():
        if isinstance(value, list):
            if value:
                print(f"{key}:")
                for v in value:
                    print(f"  - {v}")
        else:
            print(f"{key}: {value}")

    if out["citations"]:
        print(f"\n{len(out['citations'])} citations:")
        for c in out["citations"][:8]:
            print(f"  {c['field']:20s} {c['url']}")
