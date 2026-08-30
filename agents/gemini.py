"""One way in to Gemini.

Two things kept going wrong, and both come from every agent building its own
client and its own request.
"""

from __future__ import annotations

import os
from typing import Any

from google import genai
from google.genai import types

# Analysing footage is not generating it. The model is describing what a
# camera already recorded so a crew can decide whether the take is usable,
# and a film set legitimately produces violence, weapons and blood.
SAFETY = [
    types.SafetySetting(category=c, threshold="BLOCK_ONLY_HIGH")
    for c in (
        "HARM_CATEGORY_DANGEROUS_CONTENT",
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    )
]


class Blocked(RuntimeError):
    """The model returned nothing, and said why."""


_client: genai.Client | None = None


def client() -> genai.Client:
    """The one client this process uses."""
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
        )
    return _client


def use(existing: genai.Client) -> None:
    """Hand in a client, for tests, and for callers that already hold one."""
    global _client
    _client = existing


def config(**kwargs: Any) -> types.GenerateContentConfig:
    """A request config with this project's safety settings already on it."""
    kwargs.setdefault("safety_settings", SAFETY)
    return types.GenerateContentConfig(**kwargs)


def text_of(response: Any) -> str:
    """The reply, or an explanation of why there isn't one.

    Reading `.text` and moving on treats a refusal as an empty answer. Every
    caller here is deciding something about a take, so an empty answer becomes
    a missing character, a missed fault, or a scene that looks covered when it
    is not.
    """
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        feedback = getattr(response, "prompt_feedback", None)
        raise Blocked(f"nothing came back (prompt_feedback={feedback})")

    first = candidates[0]
    reason = getattr(first, "finish_reason", None)
    text = getattr(response, "text", None)

    if text:
        return text

    ratings = getattr(first, "safety_ratings", None)
    raise Blocked(f"no text, finish_reason={reason}, safety_ratings={ratings}")
