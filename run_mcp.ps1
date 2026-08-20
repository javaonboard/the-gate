# The ClickHouse MCP server the agent talks to.
#
# It connects as the read-only agent user, which can see only the curated
# marts and runs under hard query limits — ClickHouse's own guidance for
# agentic analytics. A generated query that goes wrong fails in 30 seconds
# instead of taking the cluster with it.

Get-Content .env |
  Where-Object { $_ -match '^\s*[^#].+=' } |
  ForEach-Object {
    $k, $v = $_ -split '=', 2
    Set-Item -Path "env:$k" -Value $v
  }

# point the server at the agent user and the marts, not admin and raw
$env:CLICKHOUSE_USER      = $env:CLICKHOUSE_AGENT_USER
$env:CLICKHOUSE_PASSWORD  = $env:CLICKHOUSE_AGENT_PASSWORD
$env:CLICKHOUSE_DATABASE  = $env:CLICKHOUSE_AGENT_DATABASE

$env:CLICKHOUSE_MCP_SERVER_TRANSPORT = "http"
$env:CLICKHOUSE_ALLOW_WRITE_ACCESS   = "false"
$env:CLICKHOUSE_ALLOW_DROP           = "false"
$env:CLICKHOUSE_MCP_QUERY_TIMEOUT    = "30"

Write-Host "MCP -> $env:CLICKHOUSE_DATABASE as $env:CLICKHOUSE_USER (read-only)"
mcp-clickhouse
