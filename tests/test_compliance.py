"""The rules must run before anything is said about them.

Asked "roughly what does overtime cost us", a model will answer from memory , 
measured: with AUTO it made no tool call and started estimating.
"""

from datetime import datetime

from google.genai import types

from agents import compliance
from core.union_rules import STOPS_THE_DAY


def test_the_rules_cannot_be_skipped():
    config = compliance.forced_tool_config()
    calling = config.tool_config.function_calling_config

    assert calling.mode == types.FunctionCallingConfigMode.ANY
    assert calling.allowed_function_names == [compliance.TOOL_NAME]


def test_the_tool_computes_rather_than_describes():
    """Every number comes back calculated, from the day it was given."""
    result = compliance.check_the_rules(
        "2026-08-25 07:00", "2026-08-25 21:00", "2026-08-26 07:00",
        crew_size=60, performers=3, minors=1)

    assert result["hours_on_the_clock"] == 14.0
    assert result["total_cost_usd"] > 0
    assert result["latest_clean_wrap"] < "21:00"


def test_a_minor_stops_the_day_and_money_does_not():
    long_day = ("2026-08-25 07:00", "2026-08-25 21:00", "2026-08-26 07:00")

    with_minor = compliance.check_the_rules(*long_day, minors=1)
    assert [v["rule"] for v in with_minor["stops_the_day"]] == ["minor_hours"]

    # the same day without a minor is expensive, not forbidden
    adults_only = compliance.check_the_rules(*long_day, minors=0)
    assert adults_only["stops_the_day"] == []
    assert adults_only["total_cost_usd"] > 0
    assert {v["rule"] for v in adults_only["costs_money"]} >= {
        "overtime", "meal_penalty"}


def test_one_definition_of_what_stops_a_day():
    """The gate and the steward must not disagree about this."""
    assert STOPS_THE_DAY == frozenset({"minor_hours"})
