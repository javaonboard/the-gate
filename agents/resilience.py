"""Retrying the calls that fail for no reason.

Sending video to a model means large request bodies and long-lived streams, and
those get cut. Connections reset, payloads arrive short, the service returns a
503 while it moves something around. None of it means the request was wrong, and
all of it looks like a crash if nothing catches it.

Anything that only fails transiently is retried with a widening gap. Anything
that indicates the request itself was bad — a rejected argument, a permission
problem — is raised immediately, because retrying that just wastes time and
money.
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# Worth trying again: the connection broke, or the far end was briefly unwell.
TRANSIENT = (
    "connection reset",
    "connectionreset",        # the exception class name, which has no space
    "connection aborted",
    "connectionaborted",
    "forcibly closed",        # what Windows says when the peer goes away
    "10054",
    "clientpayloaderror",
    "remoteprotocolerror",
    "incomplete",
    "payload is not completed",
    "transferencoding",
    "not enough data to satisfy transfer length",
    "server disconnected",
    "timed out",
    "timeout",
    "503",
    "502",
    "504",
    "429",
    "resource exhausted",
    "unavailable",
    "internal error",
    "deadline exceeded",
)

# Not worth trying again: the request was wrong and will be wrong next time.
PERMANENT = (
    "invalid_argument",
    "permission_denied",
    "not_found",
    "unauthenticated",
    "failed_precondition",
    "safety",
    "blocked",
)


def is_transient(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    if any(word in text for word in PERMANENT):
        return False
    return any(word in text for word in TRANSIENT)


def retry(fn: Callable[..., T], *args: Any, attempts: int = 3,
          base_delay: float = 1.5, on_retry: Callable[[int, BaseException], None] | None = None,
          **kwargs: Any) -> T:
    """Call fn, trying again if it fails in a way that might not recur.

    The gap widens each time and carries a little jitter, so several takes
    failing at once do not all come back together and fail again.
    """
    last: BaseException | None = None

    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last = exc
            if attempt == attempts or not is_transient(exc):
                raise
            if on_retry:
                on_retry(attempt, exc)
            time.sleep(base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.6))

    raise last  # unreachable, but keeps the type checker honest


async def retry_async(fn: Callable[..., Any], *args: Any, attempts: int = 3,
                      base_delay: float = 1.5,
                      on_retry: Callable[[int, BaseException], None] | None = None,
                      **kwargs: Any) -> Any:
    """The same, for the async paths — the ADK runner among them."""
    import asyncio

    last: BaseException | None = None

    for attempt in range(1, attempts + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last = exc
            if attempt == attempts or not is_transient(exc):
                raise
            if on_retry:
                on_retry(attempt, exc)
            await asyncio.sleep(
                base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.6)
            )

    raise last
