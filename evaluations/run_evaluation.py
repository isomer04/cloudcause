"""Run every seeded scenario and print the evaluation metrics.

    uv run python evaluations/run_evaluation.py

Exits non-zero when a scenario fails, so it can gate a pull request.
"""

from __future__ import annotations

import asyncio
import json
from argparse import ArgumentParser
from pathlib import Path

from cloudcause_contracts import get_settings
from harness import evaluate_all


def _write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _parse_args():
    parser = ArgumentParser(description="Run the seeded CloudCause evaluation suite.")
    parser.add_argument("--json-output", type=Path, help="Write structured results as JSON.")
    parser.add_argument("--markdown-output", type=Path, help="Write a Markdown report.")
    return parser.parse_args()


async def main(json_output: Path | None = None, markdown_output: Path | None = None) -> int:
    summary = await evaluate_all(get_settings())
    print(summary.report_text())
    if json_output:
        _write_report(json_output, json.dumps(summary.to_dict(), indent=2) + "\n")
    if markdown_output:
        _write_report(markdown_output, summary.report_markdown())
    return 0 if summary.passed == summary.total else 1


if __name__ == "__main__":
    args = _parse_args()
    raise SystemExit(asyncio.run(main(args.json_output, args.markdown_output)))
