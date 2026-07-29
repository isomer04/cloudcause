"""Read-only ``cloudcause-billing-knowledge`` MCP server.

    uv run cloudcause-mcp-knowledge

Serves the versioned rule repository. Rules are selected by the usage date being
investigated, so a rule that took effect later is never applied retroactively.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .tools import KNOWLEDGE_TOOL_ALLOWLIST, BillingKnowledgeTools

INSTRUCTIONS = """
Versioned billing knowledge: how provider billing rules, schemas, delays, prices,
and commitments must be interpreted.

Always pass the usage date of the data you are explaining. Every answer carries a
rule id, schema version, valid_from/valid_to, review date, and an official source
url. If a lookup returns warnings or is not found, say so and keep confidence low
instead of guessing.
"""


def build_server() -> tuple[FastMCP, BillingKnowledgeTools]:
    tools = BillingKnowledgeTools()
    server = FastMCP("cloudcause-billing-knowledge", instructions=INSTRUCTIONS)
    for name in KNOWLEDGE_TOOL_ALLOWLIST:
        server.add_tool(getattr(tools, name), name=name)
    return server, tools


def main() -> None:  # pragma: no cover - process entry point
    server, _ = build_server()
    server.run()


if __name__ == "__main__":  # pragma: no cover
    main()
