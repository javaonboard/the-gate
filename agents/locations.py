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

Say which labels are the same place.
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
    # First appearance order, not alphabetical: the sequence is evidence.
    seen: list[str] = []
    for raw in labels:
        lab = (raw or "").strip().lower()
        if lab and lab not in seen:
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
        labels="\n".join(f"- {lab}" for lab in labels),
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
