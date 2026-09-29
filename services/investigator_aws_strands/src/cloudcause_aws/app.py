"""AWS Strands investigator service.

    uv run cloudcause-aws-worker      # http://127.0.0.1:8101
"""

from __future__ import annotations

import os

from cloudcause_worker_core import create_worker_app

from .investigator import AwsInvestigator

app = create_worker_app(AwsInvestigator(), "CloudCause AWS investigator (Strands)")


def main() -> None:  # pragma: no cover - process entry point
    import uvicorn

    uvicorn.run(
        "cloudcause_aws.app:app",
        host=os.environ.get("CLOUDCAUSE_HOST", "127.0.0.1"),
        port=int(os.environ.get("CLOUDCAUSE_AWS_WORKER_PORT", "8101")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
