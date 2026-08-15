import os

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

load_dotenv()

root_agent = Agent(
    model=os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash"),
    name="clickhouse_probe",
    instruction=(
        "You answer questions about a ClickHouse cluster. "
        "Always use the available tools to look things up rather than guessing. "
        "Report exactly what the tools return."
    ),
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=os.environ["CLICKHOUSE_MCP_URL"],
                headers={
                    "Authorization": f"Bearer {os.environ['CLICKHOUSE_MCP_AUTH_TOKEN']}"
                },
            )
        )
    ],
)
