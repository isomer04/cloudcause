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
import tempfile
import time
from pathlib import Path
from typing import Any

from cloudcause_contracts import Settings, get_settings
from cloudcause_datasets import build_dataset_store

_SNAPSHOT_MAX_AGE_SECONDS = 60 * 60


def cleanup_server_snapshot(params: dict[str, Any]) -> None:
    """Remove a snapshot if the parent still owns it after MCP startup/session failure."""

    snapshot = str(dict(params.get("env", {})).get("CLOUDCAUSE_DATASET_SNAPSHOT", ""))
    if snapshot:
        Path(snapshot).unlink(missing_ok=True)


def _cleanup_stale_snapshots(snapshot_dir: Path) -> None:
    cutoff = time.time() - _SNAPSHOT_MAX_AGE_SECONDS
    for path in snapshot_dir.glob("dataset-*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _dataset_snapshot(dataset_id: str, settings: Settings | None = None) -> str:
    """Give a stdio child a normalized upload that otherwise lives in parent memory."""

    settings = settings or get_settings()
    store = build_dataset_store(settings)
    if store.kind != "memory":
        return ""
    dataset = store.get_for_investigation(dataset_id)
    snapshot_dir = settings.repo_root / ".cloudcause" / "mcp-snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_snapshots(snapshot_dir)
    descriptor, name = tempfile.mkstemp(prefix="dataset-", suffix=".json", dir=snapshot_dir)
    try:
        if os.name != "nt":
            os.chmod(name, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(dataset.model_dump_json())
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return name


def operational_server_params(
    provider: str,
    scenario_id: str = "default",
    dataset_id: str | None = None,
    *,
    snapshot_dataset: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Command, args, and environment for one provider's operational-data server.

    Pass the ``Settings`` the run was constructed with. Falling back to the
    environment would let an injected configuration and the process environment
    disagree about which dataset store is live, and the snapshot would then be
    written against a store the investigation is not actually reading.
    """

    snapshot = (
        _dataset_snapshot(dataset_id, settings) if dataset_id and snapshot_dataset else ""
    )
    env = {
        **os.environ,
        "CLOUDCAUSE_MCP_PROVIDER": provider,
        "CLOUDCAUSE_SCENARIO_ID": scenario_id,
        "CLOUDCAUSE_DATASET_ID": dataset_id or "",
        "CLOUDCAUSE_DATASET_SNAPSHOT": snapshot,
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
