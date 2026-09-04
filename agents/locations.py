"""Deciding when two shots were filmed in the same place.

Each shot is watched on its own, so the same corridor comes back as 'industrial
corridor', 'industrial utility corridor' and 'industrial hallway'.
"""

from __future__ import annotations

import json
import os

from google import genai
from google.genai import types

from agents import gemini

from agents.resilience import retry

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")

SCHEMA = {
    "type": "object",
    "properties": {
        "mapping": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "canonical": {"type": "string"},
                },
                "required": ["label", "canonical"],
            },
        }
    },
    "required": ["mapping"],
}

PROMPT = """These location labels were written by someone logging shots from one
film, one shot at a time. Because each shot was described on its own, the same
physical place has been given several different names.

{shots} shots, about {minutes} minutes of footage.
{known}
Every shot, in the order it was taken, with the label written for it:
{order}

A crew shoots out one place and then moves. So labels that alternate shot to
shot are one place described two or three ways — nobody carries the camera
between rooms take by take. Labels that hold for a run of shots and then never
appear again are a different place: that gap is the company moving.

This is often the only evidence there is. A scene covered entirely in close-up
gives no picture of the room it happened in, and the frames below will show you
two faces and tell you nothing. The order still tells you.

Say which labels are the same place. Give each one a canonical name, chosen
from the names already used for that place rather than invented.

Merge only what is genuinely the same place. There is no number to reach, and
collapsing the list is not the goal — the goal is that each place appears once.

The labels were written by somebody looking at the footage, so the words carry
weight. Two labels naming different kinds of place — a hallway and a warehouse,
a stairwell and a room, a doorway and a corridor — are different places, and
stay different unless the pictures plainly show one place. Grey concrete looks
like grey concrete; that is not evidence. Being in the same building is not
evidence either, and the inside and the outside of one building are two places.

What you are undoing is narrower than it looks: the same corridor written down
as 'industrial corridor', 'industrial utility corridor' and 'industrial
hallway'. Those are one place. A warehouse and a hallway are not.

Every label must appear exactly once.

A tight shot of a single object — a door handle, a phone, a pair of hands, a
prop — shows almost nothing of the room around it. That is an insert, and it
keeps its own label unless the shot genuinely shows the room it belongs to.
Guessing here silently folds a separate piece of coverage into another scene.
"""


def _ask(contents) -> dict:
    response = gemini.client().models.generate_content(
        model=MODEL,
        contents=contents,
        config=gemini.config(
            temperature=0,
            response_mime_type="application/json",
            response_schema=SCHEMA,
        ),
    )
    return json.loads(gemini.text_of(response))


def merge_labels(labels: list[str], known: list[str] | None = None,
                 shots: int = 0, minutes: float = 0.0,
                 client: genai.Client | None = None,
                 frames: dict[str, bytes] | None = None) -> dict[str, str]:
    """Map every raw label onto the place it actually is.

`known` are the places this workspace already has. A label that means one
    of them maps onto it, so footage arriving later joins the existing scene.
    """
    # The sequence is the evidence, so it is kept whole rather than reduced to
    # the set of names. Which labels exist matters less than where they sit.
    order = [(raw or "").strip().lower() for raw in labels]
    order = [lab for lab in order if lab]

    seen: list[str] = []
    for lab in order:
        if lab not in seen:
            seen.append(lab)
    labels = seen
    if len(labels) < 2:
        return {lab: lab for lab in labels}

    known = sorted({k.strip().lower() for k in (known or []) if k and k.strip()})
    known_block = ""
    if known:
        known_block = (
            "\nPlaces already in this shoot, which you should reuse by name "
            "when a label means one of them:\n"
            + "\n".join(f"- {k}" for k in known) + "\n"
        )

    prompt = PROMPT.format(
        shots=shots or len(labels),
        minutes=round(minutes, 1) if minutes else "unknown",
        known=known_block,
        order="\n".join(f"{i:3d}  {lab}"
                             for i, lab in enumerate(order, 1)),
    )

    if client is not None:
        gemini.use(client)

    # one picture per label, so the same room photographed twice is not split
    parts: list = [prompt]
    for lab in labels:
        shot = (frames or {}).get(lab)
        if shot:
            parts.append(f"This is {lab!r}:")
            parts.append(types.Part.from_bytes(data=shot, mime_type="image/png"))

    try:
        result = retry(_ask, parts)
    except Exception:
        return {lab: lab for lab in labels}

    mapping = {
        m["label"].strip().lower(): m["canonical"].strip().lower()
        for m in result.get("mapping", [])
        if m.get("label") and m.get("canonical")
    }
    # anything the model dropped keeps its own name rather than vanishing
    for lab in labels:
        mapping.setdefault(lab, lab)
    return mapping
