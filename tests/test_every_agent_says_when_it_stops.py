"""An agent that never says it finished spins until the whole run does.

The crew graph draws a spinner while an agent is working and stops it when the
agent says so. Most of the crew report through the `step` context manager,
which publishes `done` on the way out whatever happens. The intake pass
publishes by hand — and simply stopped talking when a stage ended.

That left the graph inferring an ending, and both guesses were wrong. Treating
the first tool result as the end put the Editor's spinner out while it was
still cutting, so for a minute the graph showed nobody working at all. Waiting
for the run to end instead kept every finished agent spinning to the very last
second.

Neither is fixable in the drawing. An agent has to say when it stops.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "agents" / "intake.py"

# The run itself, not a member of the crew: it brackets everything and is the
# one thing whose ending is already unambiguous.
NOT_CREW = {"orchestrator"}

# Phases that end an agent's turn. `error` counts — a stage that failed has
# stopped, and leaving it spinning claims work that is not happening.
ENDINGS = {"done", "result", "complete", "error"}


def published() -> dict[str, set[str]]:
    """Every `run.publish("<agent>", "<phase>", ...)` in the intake pass."""
    out: dict[str, set[str]] = {}
    tree = ast.parse(INTAKE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "publish"
                and len(node.args) >= 2):
            continue
        agent, phase = node.args[0], node.args[1]
        if not (isinstance(agent, ast.Constant) and isinstance(agent.value, str)
                and isinstance(phase, ast.Constant)
                and isinstance(phase.value, str)):
            continue
        out.setdefault(agent.value, set()).add(phase.value)
    return out


ALL = published()
CREW = sorted(set(ALL) - NOT_CREW)


def test_the_finder_still_finds_the_crew():
    assert len(CREW) >= 4, (
        "no agents found publishing in intake, so every assertion below would "
        "pass without checking anything"
    )


@pytest.mark.parametrize("agent", CREW)
def test_an_agent_that_starts_also_stops(agent: str):
    phases = ALL[agent]
    if not phases & {"working", "started"}:
        return
    assert phases & ENDINGS, (
        f"{agent} publishes {sorted(phases)} — it says it is working and never "
        f"says it stopped, so its spinner runs until the whole run ends. Add a "
        f"publish of 'done' where that stage actually finishes."
    )
