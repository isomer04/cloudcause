"""Stdio launch parameters for the two MCP servers.

Live agents in the Strands, MAF, and ADK services use these so every framework
reaches the same evidence boundary.

The child gets the data selector in its environment because it is a subprocess,
not an HTTP call: ``CLOUDCAUSE_SCENARIO_ID`` and, for a user's own upload,
``CLOUDCAUSE_DATASET_ID``. Without the second one a live agent's tools would
resolve the demo fixtures while its parent read the upload.
"""

from __future__ import annotations

import os
import sys
from typing import Any


def operational_server_params(
    provider: str, scenario_id: str = "default", dataset_id: str | None = None
) -> dict[str, Any]:
    """Command, args, and environment for one provider's operational-data server."""

    env = {
        **os.environ,
        "CLOUDCAUSE_MCP_PROVIDER": provider,
        "CLOUDCAUSE_SCENARIO_ID": scenario_id,
        "CLOUDCAUSE_DATASET_ID": dataset_id or "",
    }
    return {
        "command": sys.executable,
        "args": ["-m", "cloudcause_mcp.operational_server"],
        "env": env,
        "name": f"cloudcause-{provider}-operational",
    }


def knowledge_server_params() -> dict[str, Any]:
    """Command, args, and environment for the billing-knowledge server."""

    return {
        "command": sys.executable,
        "args": ["-m", "cloudcause_mcp.knowledge_server"],
        "env": dict(os.environ),
        "name": "cloudcause-billing-knowledge",
    }
