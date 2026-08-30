"""The union steward.

Which rule bites, who it costs, and how much.
"""

from __future__ import annotations

import os
from datetime import datetime

from core.union_rules import (STOPS_THE_DAY, Person, assess_day,
                              latest_wrap_without_violation)

TOOL_NAME = "check_the_rules"


def check_the_rules(call_time: str, wrap_time: str, next_call_time: str,
                    crew_size: int = 60, performers: int = 3,
                    minors: int = 0) -> dict:
    """Work out which union rules a shooting day breaks, and what they cost.

Args:
        call_time: When the crew started, as 'YYYY-MM-DD HH:MM'.
        wrap_time: When the day is expected to end, same format.
    """
    def when(text: str) -> datetime:
        return datetime.fromisoformat(text.strip().replace("/", "-"))

    call, wrap, next_call = when(call_time), when(wrap_time), when(next_call_time)

    crew = (
        [Person(f"crew_{i:02d}", "crew") for i in range(max(0, crew_size))]
        + [Person(f"cast_{i}", "cast", kind="performer", hourly_rate=180.0,
                  is_minor=i < minors)
           for i in range(max(0, performers))]
    )

    assessment = assess_day(call, wrap, crew, next_call=next_call)
    hard_stop = latest_wrap_without_violation(call, crew, next_call)

    return {
        "hours_on_the_clock": round(assessment.elapsed_hours, 1),
        "latest_clean_wrap": hard_stop.strftime("%H:%M"),
        "total_cost_usd": round(assessment.total_cost_usd),
        "stops_the_day": [
            {"rule": v.rule, "detail": v.detail, "people": v.people}
            for v in assessment.violations if v.rule in STOPS_THE_DAY
        ],
        "costs_money": [
            {"rule": v.rule, "detail": v.detail,
             "cost_usd": round(v.cost_usd), "people": v.people}
            for v in assessment.violations if v.rule not in STOPS_THE_DAY
        ],
    }


TOOLS = [check_the_rules]

INSTRUCTION = """You are the union steward on a film set.

Run the rules before you say anything about them. You do not know this
production's call time, crew size or overtime position from memory, and a
number you invent here becomes a number the 1st AD acts on.
"""


def forced_tool_config():
    """Make the rules run. The model cannot answer without calling the tool."""
    from google.genai import types

    return types.GenerateContentConfig(
        temperature=0,
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY,
                allowed_function_names=[TOOL_NAME],
            )
        ),
    )


def _declaration():
    """The tool, described for the model."""
    from google.genai import types

    return types.Tool(function_declarations=[types.FunctionDeclaration(
        name=TOOL_NAME,
        description="Work out which union rules a shooting day breaks and what "
                    "they cost.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "call_time": types.Schema(
                    type="STRING", description="'YYYY-MM-DD HH:MM'"),
                "wrap_time": types.Schema(
                    type="STRING", description="'YYYY-MM-DD HH:MM'"),
                "next_call_time": types.Schema(
                    type="STRING", description="'YYYY-MM-DD HH:MM'"),
                "crew_size": types.Schema(type="INTEGER"),
                "performers": types.Schema(type="INTEGER"),
                "minors": types.Schema(type="INTEGER"),
            },
            required=["call_time", "wrap_time", "next_call_time"]))])


def explain(call_time: str, wrap_time: str, next_call_time: str,
            crew_size: int = 60, performers: int = 3, minors: int = 0) -> dict:
    """Run the rules under forced calling, then say which one bites.

    Two round trips, both bounded. The first cannot answer without calling the
    tool; the second has the answer and no tools at all, so it has to write
    something.
    """
    from google.genai import types

    from agents import gemini

    asked = (f"Call {call_time}, expected wrap {wrap_time}, back at "
             f"{next_call_time}. {crew_size} crew, {performers} performers, "
             f"{minors} of them under 18. Where do we stand?")

    first = gemini.client().models.generate_content(
        model=os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash"),
        contents=asked,
        config=gemini.config(
            temperature=0,
            system_instruction=INSTRUCTION,
            tools=[_declaration()],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY,
                    allowed_function_names=[TOOL_NAME]))))

    wanted = first.candidates[0].content.parts[0].function_call
    args = dict(wanted.args or {})
    args.setdefault("call_time", call_time)
    args.setdefault("wrap_time", wrap_time)
    args.setdefault("next_call_time", next_call_time)
    facts = check_the_rules(**args)

    # tools off, so this turn has to produce words rather than another call
    second = gemini.client().models.generate_content(
        model=os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash"),
        contents=[
            types.Content(role="user", parts=[types.Part(text=asked)]),
            first.candidates[0].content,
            types.Content(role="user", parts=[types.Part.from_function_response(
                name=TOOL_NAME, response=facts)]),
        ],
        config=gemini.config(temperature=0, system_instruction=INSTRUCTION))

    return {"facts": facts, "said": (second.text or "").strip(),
            "tool_called": wanted.name}


def build_agent(callbacks: dict | None = None):
    from google.adk.agents import Agent

    return Agent(
        model=os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash"),
        name="steward",
        description="Says which union rule bites, what it costs, and whether "
                    "it stops the day.",
        instruction=INSTRUCTION,
        tools=TOOLS,
        generate_content_config=forced_tool_config(),
        **(callbacks or {}),
    )
