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
    """The connection this process uses. Opened once, kept, replaced if it dies.

    ClickHouse Cloud puts an idle service to sleep, and the socket this holds
    dies with it. Nothing noticed: the next query sat on a dead connection
    until the driver's own five-minute patience ran out, so the first visitor
    after a quiet spell watched a request hang for minutes and left. The logs
    showed one of them give up fourteen seconds in, forty seconds before the
    answer arrived.

    So the connection is now checked before it is handed out, and a dead one
    is thrown away rather than used. Waking the service is still slow — that
    is ClickHouse's to do — but it is now a wait that ends.
    """
    global _client
    if _client is not None:
        try:
            _client.ping()
        except Exception:
            try:
                _client.close()
            except Exception:
                pass
            _client = None
    if _client is None:
        _client = connect()
    return _client
