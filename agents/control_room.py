"""Video Village — putting the call where the crew already looks.

A decision nobody sees is not a decision. This writes to Grafana: a marker on
the timeline whenever the call changes, and an incident when the day is going
to break. The crew already has Grafana on the wall; we do not ask them to watch
another screen.

Annotations use the Grafana HTTP API with a service account token, which works
unattended — the hosted MCP endpoint needs an interactive browser login and so
cannot run on Cloud Run. The MCP connection is offered for the agent to explore
dashboards and alerts during development.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()


def _base() -> str:
    url = os.environ.get("GRAFANA_URL", "").rstrip("/")
    if url and not url.startswith("http"):
        url = "https://" + url
    return url


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['GRAFANA_SERVICE_ACCOUNT_TOKEN']}",
        "Content-Type": "application/json",
    }


# --- tools ------------------------------------------------------------------

def mark_timeline(text: str, tags: list[str] | None = None,
                  panel_id: int = 0) -> dict[str, Any]:
    """Put a marker on the Grafana timeline.

    Use whenever something changes that someone might later ask about — the call
    flipped, a setup finished, weather arrived, the odds moved.

    Args:
        text: What happened, in plain words.
        tags: Short labels, e.g. ["gate", "no-go"].
        panel_id: Optional dashboard panel to attach to.
    """
    body: dict[str, Any] = {"text": text, "tags": tags or ["the-gate"]}
    if panel_id:
        body["panelId"] = panel_id

    r = httpx.post(f"{_base()}/api/annotations", headers=_headers(),
                   json=body, timeout=15.0)
    r.raise_for_status()
    return {"ok": True, "id": r.json().get("id"), "text": text}


def raise_the_alarm(title: str, severity: str = "minor",
                    detail: str = "") -> dict[str, Any]:
    """Declare a Grafana incident because the day is going to break.

    Use when the call is NO-GO and the clock will not allow a fix, or when a
    union rule is about to be broken. This pages the people who can act.

    Args:
        title: One line, e.g. "Scene 42 short one angle, 22 minutes of light".
        severity: "critical", "major", "minor".
        detail: Longer explanation, including the numbers behind the call.
    """
    url = (f"{_base()}/api/plugins/grafana-incident-app/resources/api/"
           f"IncidentsService.CreateIncident")
    try:
        r = httpx.post(url, headers=_headers(),
                       json={"title": title, "severity": severity,
                             "roomPrefix": "the-gate", "isDrill": False,
                             "status": "active", "attachCaption": detail},
                       timeout=20.0)
        r.raise_for_status()
        data = r.json()
        return {"ok": True, "incident": data.get("incident", data)}
    except httpx.HTTPStatusError as exc:
        # IRM may not be on this tier. Fall back so the day is still recorded.
        mark_timeline(f"INCIDENT: {title} — {detail}"[:500],
                      tags=["the-gate", "incident", severity])
        return {"ok": False, "fell_back_to_annotation": True,
                "status": exc.response.status_code}


def note_on_incident(incident_id: str, text: str) -> dict[str, Any]:
    """Add to an open incident as the situation develops.

    Args:
        incident_id: The incident to add to.
        text: What has changed.
    """
    url = (f"{_base()}/api/plugins/grafana-incident-app/resources/api/"
           f"ActivityService.AddActivity")
    r = httpx.post(url, headers=_headers(),
                   json={"incidentID": incident_id, "activityKind": "userNote",
                         "body": text},
                   timeout=20.0)
    r.raise_for_status()
    return {"ok": True}


def list_alert_rules() -> list[dict[str, Any]]:
    """What is currently being watched."""
    r = httpx.get(f"{_base()}/api/v1/provisioning/alert-rules",
                  headers=_headers(), timeout=15.0)
    r.raise_for_status()
    return [
        {"uid": rule.get("uid"), "title": rule.get("title"),
         "folder": rule.get("folderUID")}
        for rule in r.json()
    ]


TOOLS = [mark_timeline, raise_the_alarm, note_on_incident, list_alert_rules]


# --- the agent --------------------------------------------------------------

INSTRUCTION = """You run video village. Your job is to make sure the right
people see the right thing at the right moment, and nothing else.

Rules:
- Mark the timeline whenever the call changes or something happens the crew may
  ask about later. Keep the text short and readable by someone who was not there.
- Raise the alarm only when the day is genuinely going to break: the call is
  NO-GO and there is not time to fix it, or a union rule is about to go. A
  production that gets paged for everything stops reading pages.
- Always include the numbers. "Two minutes over" means nothing; "wrap 20:14
  against a 20:00 hard stop, $2,000" can be acted on.
- Never invent a figure. Use what the rest of the crew gave you."""


def build_agent(callbacks: dict | None = None, with_mcp: bool = False):
    """Video Village as an ADK agent.

    with_mcp attaches Grafana's hosted MCP server. That uses an interactive
    OAuth login, so it is for development only — the deployed agent uses the
    service account token through the tools above.
    """
    from google.adk.agents import Agent

    tools = list(TOOLS)

    if with_mcp:
        from google.adk.tools.mcp_tool import McpToolset
        from google.adk.tools.mcp_tool.mcp_session_manager import (
            StreamableHTTPConnectionParams,
        )

        tools.append(
            McpToolset(
                connection_params=StreamableHTTPConnectionParams(
                    url="https://mcp.grafana.com/mcp",
                    headers={"X-Grafana-URL": _base()},
                )
            )
        )

    return Agent(
        model=os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash"),
        name="control_room",
        description="Puts the call on screen and raises the alarm.",
        instruction=INSTRUCTION,
        tools=tools,
        **(callbacks or {}),
    )


if __name__ == "__main__":
    print("marking the timeline…")
    print(mark_timeline(
        "THE GATE — scene prod_now_sc001 came back NO-GO, short a single on the "
        "third actor. 72% chance of making the day.",
        tags=["the-gate", "no-go", "canal-street"],
    ))

    print("\nalert rules currently provisioned:")
    for rule in list_alert_rules():
        print(f"  {rule['title']}")
