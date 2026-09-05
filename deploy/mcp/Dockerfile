# The ClickHouse MCP server, as its own Cloud Run service.
#
# It has to be self-hosted rather than ClickHouse's managed endpoint, and the
# reason is the same one Grafana's hosted MCP has: the managed server signs in
# through a browser OAuth flow, and nothing running unattended can complete
# one. The managed endpoint stays the better one to demo against by hand.
#
# What it serves is not the database. It signs in as the read-only agent user,
# which can see only the five curated views in the_gate_marts and runs under
# hard query limits. A SELECT on a raw table is refused by ClickHouse itself,
# so the boundary holds whatever the agent asks for.

FROM python:3.12-slim

# Pinned to the version everything here was built and proved
# against. Left open, the image took a newer one whose host
# allowlist refuses to start behind a wildcard bind — the server
# on this desk and the server in production were different code,
# and the difference showed up in production.
RUN pip install --no-cache-dir mcp-clickhouse==0.4.1

# HTTP rather than stdio: a subprocess inside a managed runtime is the trap
# the official MCP tutorial exists to avoid. Bound to every interface because
# Cloud Run reaches the container from outside it, and to the port Cloud Run
# hands us rather than the server's own default.
ENV CLICKHOUSE_MCP_SERVER_TRANSPORT=http \
    CLICKHOUSE_MCP_BIND_HOST=0.0.0.0 \
    CLICKHOUSE_ALLOW_WRITE_ACCESS=false \
    CLICKHOUSE_ALLOW_DROP=false \
    CLICKHOUSE_MCP_QUERY_TIMEOUT=30 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

# Named for the versions that ask for it when the bind host is a
# wildcard, which is every Cloud Run container: it is reached from
# outside itself.
ENV CLICKHOUSE_MCP_ALLOWED_HOSTS=the-gate-mcp-524607351626.us-central1.run.app

# The server reads its port from CLICKHOUSE_MCP_BIND_PORT, and Cloud Run says
# which one in PORT, so the two are joined here rather than assumed equal.
CMD ["sh", "-c", "CLICKHOUSE_MCP_BIND_PORT=${PORT} exec mcp-clickhouse"]
