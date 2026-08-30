"""What every route module needs.

One ClickHouse connection, one database name, and the setting that makes a
mutation finish before the request answers, a route that reports a change and
returns before it has happened shows the old state back on the next reload.
"""

from __future__ import annotations

import os

from core.coverage import connect

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")

# ClickHouse mutations run in the background unless told otherwise.
DONE_BEFORE_REPLYING = {"mutations_sync": 2}

_client = None


def client():
    """The connection this process uses. Opened once, kept."""
    global _client
    if _client is None:
        _client = connect()
    return _client
