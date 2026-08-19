"""The Gate — the production desk.

Runs the crew and puts the call together.

The shape matters. Facts that have a correct answer are computed in Python and
handed to the agents as evidence: coverage, the odds, what a penalty costs. The
agents decide what to look at, read what comes back, and say what it means. No
agent is ever asked to produce a number that could be calculated.

Three ways in, all landing here:
  a setup finishes          — new footage on the card
  the clock ticks           — Cloud Scheduler
  the world changes         — a Parallel Monitor webhook
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv

from agents import control_room, historian, scout
from api.events import Run, adk_callbacks, step
from core.coverage import connect
from core.gate import GateReport, build_report
from core.simulator import pending_setups
from core.union_rules import Person

load_dotenv()

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")


@dataclass
class Trigger:
    """Why we are looking at this scene right now."""

    kind: str            # footage | schedule | world
    detail: str = ""

    @property
    def headline(self) -> str:
        return {
            "footage": "A setup finished",
            "schedule": "Routine check",
            "world": "Something changed outside",
        }.get(self.kind, "Checking the gate")


INSTRUCTION = """You are the production desk on a film set. The 1st AD is
standing in front of you and the crew is waiting to move the camera.

You will be given evidence that has already been worked out:
- what the scene needs and what is on the card
- the odds of making the day, from ten thousand simulations
- what each missing shot costs to grab now against what it costs to come back for
- anything the scout found happening outside

Your job is to give the call, in the fewest words that could change what they do.

Rules:
- Lead with GO or NO-GO. Never bury it.
- If something is missing, say what, and give both prices. That comparison is
  the whole decision.
- Use the numbers you were given. Never produce one of your own — if you find
  yourself estimating, you are doing the wrong job.
- Speak like a person on a set, not a report. "You're short a single on Marcus.
  Grab it — two grand tonight against thirty for a pickup day."
- If everything is covered and the day holds, say so in one line and stop.
- Refer to people by name. Lind, not dp_lind.

You may call the crew for anything the evidence does not cover. The scout knows
what is happening outside. The book knows what this crew has done before."""


def demo_crew() -> list[Person]:
    return (
        [Person(f"camera_{i:02d}", "camera") for i in range(18)]
        + [Person(f"cast_{i}", "cast", kind="performer", hourly_rate=180.0)
           for i in range(3)]
    )


def build_agent(run: Run | None = None, with_mcp: bool = True):
    """The full crew, as an ADK agent with sub-agents."""
    from google.adk.agents import Agent

    def cb(key: str) -> dict:
        return adk_callbacks(run, key) if run else {}

    return Agent(
        model=os.environ.get("GEMINI_MODEL_PRO",
                             os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash")),
        name="the_gate",
        description="Decides whether the crew can move on.",
        instruction=INSTRUCTION,
        sub_agents=[
            scout.build_agent(cb("scout")),
            historian.build_agent(cb("historian"), with_mcp=with_mcp),
            control_room.build_agent(cb("control_room")),
        ],
        **cb("orchestrator"),
    )


def gather_evidence(client, scene_id: str, now: datetime, call: datetime,
                    next_call: datetime, run: Run | None = None,
                    trials: int = 10_000) -> GateReport:
    """Everything with a correct answer, computed before any agent speaks."""

    with step(run, "vision", "Reading the day's takes") as s:
        takes = client.query(
            f"SELECT count() FROM {DB}.take_analysis WHERE scene_id = %(s)s",
            parameters={"s": scene_id},
        ).result_rows[0][0]
        s.result(f"{takes} takes logged so far", {"takes": takes})

    with step(run, "script", "Working out what this scene needs") as s:
        reqs = client.query(
            f"SELECT count() FROM {DB}.scene_requirements WHERE scene_id = %(s)s",
            parameters={"s": scene_id},
        ).result_rows[0][0]
        s.result(f"{reqs} shots the editor will need", {"requirements": reqs})

    all_setups = pending_setups(client, scene_id)
    done = {s.setup_id for s in all_setups[: (len(all_setups) * 2) // 3]}
    remaining = [s for s in all_setups if s.setup_id not in done]

    with step(run, "historian", "Looking up how long this crew usually takes") as s:
        s.tool("clickhouse.run_query", "Reading four years of setup times")
        if remaining:
            first = remaining[0]
            past = historian.how_long_does_this_take(
                first.dp_id, first.int_ext, first.day_night, first.scene_type)
            s.result(
                f"{past['typical_minutes']} min typical, "
                f"{past.get('slow_day_minutes', '?')} on a slow day "
                f"({past['setups_observed']} setups)",
                past,
            )

    with step(run, "simulator", f"Running {trials:,} versions of the rest of the day"):
        report = build_report(
            client, scene_id, now=now, call=call, crew=demo_crew(),
            next_call=next_call, completed_setup_ids=done, trials=trials,
        )

    with step(run, "compliance", "Checking rest, meals and overtime") as s:
        s.result(f"Latest clean wrap {report.baseline.hard_stop:%H:%M}",
                 {"hard_stop": report.baseline.hard_stop.isoformat()})

    with step(run, "planner", "Pricing what is missing") as s:
        s.result(report.summary(), {"options": len(report.options)})

    return report


def evidence_brief(report: GateReport, trigger: Trigger) -> str:
    """The computed facts, written out for the agent to reason over."""
    b = report.baseline
    lines = [
        f"Trigger: {trigger.headline}. {trigger.detail}".strip(),
        f"Scene {report.scene_id} at {report.now:%H:%M}.",
        "",
        f"Coverage: {report.coverage.completeness:.0%} "
        f"({len(report.coverage.takes)} takes logged).",
        f"Odds of making the day: {b.p_make_the_day:.0%} over {b.trials:,} runs.",
        f"Hard stop {b.hard_stop:%H:%M}. Likely wrap {b.median_wrap:%H:%M}, "
        f"{b.p90_wrap:%H:%M} on a slow finish.",
        f"Penalty already expected: ${b.expected_penalty_usd:,.0f}.",
        f"{len(b.setups)} setups still to shoot.",
    ]

    if report.options:
        lines += ["", "Missing, priced both ways:"]
        for o in sorted(report.options, key=lambda o: -o.saving_usd):
            name = f"{o.requirement.shot_type} {o.requirement.subject}".strip()
            lines.append(
                f"- {name}: ${o.shoot_now_usd:,.0f} to shoot now, "
                f"${o.recover_later_usd:,} to pick up later, "
                f"saving ${o.saving_usd:,.0f}. "
                f"Odds fall to {o.p_make_day_after:.0%} if shot."
            )
    else:
        lines += ["", "Nothing missing."]

    return "\n".join(lines)


async def run_gate(scene_id: str, now: datetime, call: datetime,
                   next_call: datetime, run: Run | None = None,
                   trigger: Trigger | None = None,
                   use_agent: bool = True) -> dict[str, Any]:
    """One full pass: compute the facts, then let the crew speak."""
    trigger = trigger or Trigger("schedule")
    client = connect()

    # Off the event loop. The queries and the simulation block, and if they run
    # on the loop nothing reaches the browser until the whole call is finished
    # — the crew appears to do a day's work in a single instant.
    report = await asyncio.to_thread(
        gather_evidence, client, scene_id, now, call, next_call, run
    )
    brief = evidence_brief(report, trigger)
    spoken = report.summary()

    if use_agent:
        try:
            from google.adk.runners import InMemoryRunner
            from google.genai import types

            with step(run, "orchestrator", "Making the call") as s:
                agent = build_agent(run)
                runner = InMemoryRunner(agent=agent, app_name="the_gate")
                session = await runner.session_service.create_session(
                    app_name="the_gate", user_id="1st_ad")

                said = []
                async for event in runner.run_async(
                    user_id="1st_ad",
                    session_id=session.id,
                    new_message=types.Content(
                        role="user", parts=[types.Part(text=brief)]),
                ):
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if getattr(part, "text", None):
                                said.append(part.text)

                if said:
                    spoken = "".join(said).strip()
                s.result(spoken)
        except Exception as exc:  # the computed call still stands
            if run:
                run.publish("orchestrator", "error",
                            f"Falling back to the computed call: {exc}")

    if run:
        run.publish("control_room", "tool_call",
                    "Marking the timeline in Grafana",
                    {"tool": "grafana.mark_timeline"})
    try:
        control_room.mark_timeline(
            f"{report.verdict} — {spoken}"[:500],
            tags=["the-gate", report.verdict.lower(), scene_id],
        )
        if run:
            run.publish("control_room", "tool_result", "Timeline marked")
    except Exception as exc:
        if run:
            run.publish("control_room", "error", f"Grafana write failed: {exc}")

    return {"report": report, "spoken": spoken, "brief": brief}


if __name__ == "__main__":
    import asyncio
    import sys

    from api.events import bus

    scene = sys.argv[1] if len(sys.argv) > 1 else "prod_now_sc001"
    hours_in = float(sys.argv[2]) if len(sys.argv) > 2 else 9.0

    call_time = datetime(2026, 8, 16, 7, 0)
    run = bus.start(scene)

    result = asyncio.run(run_gate(
        scene,
        now=call_time + timedelta(hours=hours_in),
        call=call_time,
        next_call=call_time + timedelta(days=1, hours=1),
        run=run,
        trigger=Trigger("schedule", "Between setups."),
    ))

    print("\n--- crew ---")
    for event in run.history:
        label = event.to_dict()
        print(f"  {label['agent_name']:14s} {event.phase:12s} {event.message}")

    print("\n--- the call ---")
    print(result["spoken"])
