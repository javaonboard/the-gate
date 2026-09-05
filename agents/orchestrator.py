"""The Gate, the production desk.

Runs the crew and puts the call together.
  """

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv

from agents import compliance, historian, scout
from agents.resilience import is_transient
from api.events import Run, adk_callbacks, step
from core.coverage import connect
from core.gate import GateReport, build_report, production_of
from core.simulator import day_setups
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
- how long is left before the crew are owed their rest, and how many of the
  missing shots fit in it
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


def crew_for(client, scene_id: str) -> list[Person]:
    """Who is on the clock for the day this scene belongs to.

    Every penalty is a headcount times a rate, so this is the multiplier on the
    whole money axis. It used to be eighteen camera and three cast written into
    the source, which made the figures right for one kind of production and
    wrong for every other.
    """
    from core.crew import people_of

    where = client.query(
        f"SELECT production_id FROM {DB}.scenes WHERE scene_id = %(s)s LIMIT 1",
        parameters={"s": scene_id},
    ).result_rows
    return people_of(where[0][0] if where else "")


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
        ],
        **cb("orchestrator"),
    )


def gather_evidence(client, scene_id: str, now: datetime, call: datetime,
                    next_call: datetime, run: Run | None = None,
                    trials: int = 10_000) -> GateReport:
    """Everything with a correct answer, computed before any agent speaks."""

    # Four questions that do not depend on each other: what is on the
    # card, what the scene needs, what is happening outside, and how long
    # this crew takes.
    def count_takes():
        with step(run, "vision", "Reading the day's takes") as s:
            n = client.query(
                f"SELECT count() FROM {DB}.take_analysis WHERE scene_id = %(s)s",
                parameters={"s": scene_id},
            ).result_rows[0][0]
            s.result(f"{n} takes logged so far", {"takes": n})
            return n

    def count_requirements():
        with step(run, "script", "Working out what this scene needs") as s:
            n = client.query(
                f"SELECT count() FROM {DB}.scene_requirements WHERE scene_id = %(s)s",
                parameters={"s": scene_id},
            ).result_rows[0][0]
            s.result(f"{n} shots the editor will need", {"requirements": n})
            return n

    def look_outside():
        """Whether the street is closed does not depend on a model asking."""
        with step(run, "scout", "Checking what is happening at the location") as s:
            s.tool("parallel.search", "Closures, permits and events near the set")
            try:
                where = client.query(
                    f"SELECT location_id FROM {DB}.scenes "
                    f"WHERE scene_id = %(s)s LIMIT 1",
                    parameters={"s": scene_id},
                ).result_rows
                outside = scout.conditions_at(
                    client, where[0][0] if where else "location",
                    now.date().isoformat())
                s.result(outside["summary"][:160], outside)
            except Exception as exc:
                # The world not answering is not a reason to withhold the call.
                s.result(f"Could not reach the outside world "
                         f"({type(exc).__name__})", {"error": True})

    def ask_the_book(setup):
        with step(run, "historian",
                  "Looking up how long this crew usually takes") as s:
            s.tool("clickhouse.run_query", "Reading four years of setup times")
            if setup is None:
                return
            past = historian.how_long_does_this_take(
                setup.dp_id, setup.int_ext, setup.day_night, setup.scene_type)
            s.result(
                f"{past['typical_minutes']} min typical, "
                f"{past.get('slow_day_minutes', '?')} on a slow day "
                f"({past['setups_observed']} setups)",
                past,
            )

    # What is left to shoot, read off the footage: a setup with takes against
    # it has been shot. This used to call the first two thirds of the list
    # done, which was a stand-in from before there was anything real to read —
    # it counted finished work as pending and the odds of making the day came
    # out at nothing however much of the card had been shot.
    remaining = day_setups(client, production_of(client, scene_id))

    with ThreadPoolExecutor(max_workers=4) as pool:
        waiting = [
            pool.submit(count_takes),
            pool.submit(count_requirements),
            pool.submit(look_outside),
            pool.submit(ask_the_book, remaining[0] if remaining else None),
        ]
        for job in waiting:
            job.result()

    crew = crew_for(client, scene_id)

    with step(run, "simulator", f"Running {trials:,} versions of the rest of the day"):
        report = build_report(
            client, scene_id, now=now, call=call, crew=crew,
            next_call=next_call, trials=trials,
        )

    with step(run, "compliance", "Checking rest, meals and overtime") as s:
        s.tool("union_rules.assess_day", "Meals, turnaround, overtime, minors")
        cost = report.compliance.total_cost_usd if report.compliance else 0
        s.result(f"Latest clean wrap {report.baseline.hard_stop:%H:%M}", {
            "hard_stop": report.baseline.hard_stop.isoformat(),
            "cost_usd": round(cost),
            "stops_the_day": [v.rule for v in report.blocked_by_rule],
        })

    with step(run, "planner", "Pricing what is missing") as s:
        s.result(report.summary(), {"options": len(report.options)})

    return report


def evidence_brief(report: GateReport, trigger: Trigger) -> str:
    """The computed facts, written out for the agent to reason over."""
    b = report.baseline
    lines = [
        # The verdict first, and stated as settled.
        f"THE CALL IS {report.verdict}. This is already decided. Report it.",
        "",
        f"Trigger: {trigger.headline}. {trigger.detail}".strip(),
        f"Scene {report.scene_id} at {report.now:%H:%M}.",
        "",
        (f"Coverage: {report.coverage.completeness:.0%} — "
         f"{report.coverage.summary['have']} of "
         f"{report.coverage.summary['required']} shots across "
         f"{report.coverage.people} people on camera."
         if report.coverage.judged else
         "Coverage: NOT JUDGED. Nobody has said what this scene needs — there "
         "are no people identified on camera and no shots asked of the scene "
         "itself. Say exactly that. Do not say it is covered, do not say it is "
         "fine, and do not imply the crew can move on. Tell them to say what "
         "the scene needs."),
        # Time and room, not a probability.
        #
        # The odds were the headline here and they are the wrong headline once
        # the call sheet is finished: nothing left to shoot reads as a hundred
        # per cent, so the crew said "100% chance of making the day" over a
        # scene ten shots short, and then "odds drop to 100% if you shoot it".
        # What is left is a deadline and how much fits inside it.
        f"{report.minutes_left:.0f} minutes until the crew are owed their "
        f"rest at {b.hard_stop:%H:%M}. Wrapping later costs double time.",
        f"{report.room_for} of the {len(report.coverage.missing())} missing "
        f"shots fit in that, taking the most valuable first.",
        f"Penalty already expected: ${b.expected_penalty_usd:,.0f}.",
        (f"{len(b.setups)} setups still to shoot."
         if b.setups else
         "Everything on the call sheet is shot. What is left is the coverage "
         "the scenes are short, not the schedule."),
    ]

    if report.options:
        lines += ["", "Missing, priced both ways:"]
        for o in sorted(report.options, key=lambda o: -o.saving_usd):
            name = o.requirement.describe
            lines.append(
                f"- {name}: ${o.shoot_now_usd:,.0f} to shoot now, "
                f"${o.recover_later_usd:,} to pick up later, "
                f"saving ${o.saving_usd:,.0f}. "
                f"Takes about {o.minutes:.0f} minutes."
            )
    elif report.coverage.missing():
        # Missing, but with nothing left on the schedule to hang a
        # recovery option on.
        lines += ["", "Missing, and no setup left today to fold them into:"]
        for m in sorted(report.coverage.missing(),
                        key=lambda m: -m.recover_cost_usd):
            lines.append(f"- {m.describe}: ${m.recover_cost_usd:,} to come back for.")
    else:
        lines += ["", "Nothing missing."]

    if report.blocked_by_rule:
        lines += ["", "Stops the day:"]
        for v in report.blocked_by_rule:
            lines.append(f"- {v.rule}: {v.detail}")

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
    #, the crew appears to do a day's work in a single instant.
    report = await asyncio.to_thread(
        gather_evidence, client, scene_id, now, call, next_call, run
    )
    brief = evidence_brief(report, trigger)
    spoken = report.summary()

    if use_agent and (report.blocked_by_rule
                      or (report.compliance
                          and report.compliance.total_cost_usd > 0)):
        # Only when something is actually wrong. A clean day needs no
        # explaining, and you do not call the steward over to be told nothing
        # is the matter.
        with step(run, "compliance", "Asking the steward what this costs") as s:
            s.tool("check_the_rules", "Forced: the rules run before he speaks")
            try:
                said = await asyncio.to_thread(
                    compliance.explain,
                    call.strftime("%Y-%m-%d %H:%M"),
                    report.baseline.median_wrap.strftime("%Y-%m-%d %H:%M"),
                    next_call.strftime("%Y-%m-%d %H:%M"),
                    crew_size=max((v.people for v in
                                   (report.compliance.violations
                                    if report.compliance else [])), default=0),
                )
                s.result(said["said"] or "Rules checked.", said["facts"])
            except Exception as exc:
                s.result(f"Steward unavailable ({type(exc).__name__}), "
                         f"the computed figures stand")

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
        except Exception as exc:
            # The numbers are already worked out; only the wording is lost.
            # Say which, so a dropped connection is not mistaken for a bad call.
            if run:
                reason = ("the model connection dropped" if is_transient(exc)
                          else f"{type(exc).__name__}")
                run.publish("orchestrator", "working",
                            f"Reporting the computed call, {reason}")

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
