"""Azure MAF investigator service.

    uv run cloudcause-azure-worker      # http://127.0.0.1:8102
"""

from __future__ import annotations

import os

from cloudcause_worker_core import create_worker_app

from .investigator import AzureInvestigator

app = create_worker_app(AzureInvestigator(), "CloudCause Azure investigator (MAF)")


def main() -> None:  # pragma: no cover - process entry point
    import uvicorn

    uvicorn.run(
        "cloudcause_azure.app:app",
        host=os.environ.get("CLOUDCAUSE_HOST", "127.0.0.1"),
        port=int(os.environ.get("CLOUDCAUSE_AZURE_WORKER_PORT", "8102")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
