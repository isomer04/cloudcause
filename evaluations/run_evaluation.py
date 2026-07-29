"""Run every seeded scenario and print the evaluation metrics.

    uv run python evaluations/run_evaluation.py

Exits non-zero when a scenario fails, so it can gate a pull request.
"""

from __future__ import annotations

import asyncio

from cloudcause_contracts import get_settings
from harness import evaluate_all


async def main() -> int:
    summary = await evaluate_all(get_settings())
    print(summary.report_text())
    return 0 if summary.passed == summary.total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
