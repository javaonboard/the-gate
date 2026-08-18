"""Live activity bus.

Every agent and every deterministic step publishes what it is doing. The
interface subscribes over Server-Sent Events and shows a status rail — which
member of the crew is working, on what, and what came back.

Deliberately not a chat. It is a production status board.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from api.labels import agent_label

# phases an agent moves through
STARTED = "started"
WORKING = "working"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
DONE = "done"
ERROR = "error"

MAX_HISTORY = 500


@dataclass
class AgentEvent:
    run_id: str
    agent: str
    phase: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    ts: str = ""
    elapsed_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        label = agent_label(self.agent)
        d["agent_name"] = label["name"]
        d["agent_role"] = label["role"]
        return d


class Run:
    """One pass of the crew over a scene."""

    def __init__(self, run_id: str, scene_id: str):
        self.run_id = run_id
        self.scene_id = scene_id
        self.started_at = time.monotonic()
        self.history: deque[AgentEvent] = deque(maxlen=MAX_HISTORY)
        self.subscribers: set[asyncio.Queue] = set()
        self.seq = 0
        self.finished = False

    def publish(self, agent: str, phase: str, message: str,
                data: dict[str, Any] | None = None) -> AgentEvent:
        self.seq += 1
        event = AgentEvent(
            run_id=self.run_id,
            agent=agent,
            phase=phase,
            message=message,
            data=data or {},
            seq=self.seq,
            ts=datetime.now(timezone.utc).isoformat(),
            elapsed_ms=int((time.monotonic() - self.started_at) * 1000),
        )
        self.history.append(event)
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        for event in self.history:
            q.put_nowait(event)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def finish(self) -> None:
        self.finished = True
        self.publish("orchestrator", DONE, "Call complete")


class Bus:
    """All runs, most recent first."""

    def __init__(self) -> None:
        self.runs: dict[str, Run] = {}
        self.order: deque[str] = deque(maxlen=50)

    def start(self, scene_id: str) -> Run:
        run_id = uuid.uuid4().hex[:8]
        run = Run(run_id, scene_id)
        self.runs[run_id] = run
        self.order.appendleft(run_id)
        run.publish("orchestrator", STARTED, f"Checking the gate on {scene_id}")
        return run

    def get(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    def latest(self) -> Run | None:
        return self.runs.get(self.order[0]) if self.order else None


bus = Bus()


class step:
    """Context manager that brackets a piece of work with events.

        with step(run, "simulator", "Running 10,000 trials") as s:
            ...
            s.result("72% chance of making the day", {"p": 0.72})
    """

    def __init__(self, run: Run | None, agent: str, message: str,
                 data: dict[str, Any] | None = None):
        self.run = run
        self.agent = agent
        self.message = message
        self.data = data or {}
        self.started = 0.0

    def __enter__(self) -> "step":
        self.started = time.monotonic()
        if self.run:
            self.run.publish(self.agent, WORKING, self.message, self.data)
        return self

    def tool(self, name: str, message: str, data: dict[str, Any] | None = None) -> None:
        if self.run:
            self.run.publish(self.agent, TOOL_CALL, message,
                             {"tool": name, **(data or {})})

    def result(self, message: str, data: dict[str, Any] | None = None) -> None:
        if self.run:
            self.run.publish(self.agent, TOOL_RESULT, message, data or {})

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not self.run:
            return False
        took = int((time.monotonic() - self.started) * 1000)
        if exc_type is not None:
            self.run.publish(self.agent, ERROR, str(exc), {"took_ms": took})
        else:
            self.run.publish(self.agent, DONE, self.message, {"took_ms": took})
        return False


def sse(event: AgentEvent) -> str:
    """Format one event for an EventSource stream."""
    return f"event: agent\ndata: {json.dumps(event.to_dict())}\n\n"


# --- ADK callbacks ----------------------------------------------------------

def adk_callbacks(run: Run, agent_key: str) -> dict[str, Any]:
    """Callbacks to attach to an ADK Agent so its work appears on the rail."""

    def before_agent(callback_context=None, **_):
        run.publish(agent_key, STARTED, agent_label(agent_key)["does"])

    def after_agent(callback_context=None, **_):
        run.publish(agent_key, DONE, "Finished")

    def before_tool(tool=None, args=None, **_):
        name = getattr(tool, "name", "tool")
        run.publish(agent_key, TOOL_CALL, f"Calling {name}",
                    {"tool": name, "args": _small(args)})

    def after_tool(tool=None, tool_response=None, **_):
        name = getattr(tool, "name", "tool")
        run.publish(agent_key, TOOL_RESULT, f"{name} returned",
                    {"tool": name, "preview": _small(tool_response)})

    return {
        "before_agent_callback": before_agent,
        "after_agent_callback": after_agent,
        "before_tool_callback": before_tool,
        "after_tool_callback": after_tool,
    }


def _small(value: Any, limit: int = 400) -> Any:
    """Keep payloads on the rail readable."""
    try:
        text = json.dumps(value, default=str)
    except Exception:
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"
