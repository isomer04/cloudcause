"""Scheduled documentation-change check (docs/billing-knowledge.md section 5).

    uv run python scripts/check_provider_docs.py
    uv run python scripts/check_provider_docs.py --update-baseline   # after human review

Fetches the official channels listed in ``knowledge/monitored_sources.yaml``,
hashes their text, and compares against ``knowledge/sources_baseline.json``.

What this does NOT do, on purpose:

* It never edits a billing rule, adapter, fixture, or test.
* It never decides whether a change is editorial, regional, preview-only, a
  deprecation, or a real billing-rule change. A human does that.

Exit codes: 0 no change, 2 changes detected, 1 the check itself failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from cloudcause_contracts import find_repo_root

USER_AGENT = "CloudCause-DocWatch/0.1 (+https://focus.finops.org/focus-specification/)"
TIMEOUT_SECONDS = 30
TAG_PATTERN = re.compile(r"<[^>]+>")
SCRIPT_PATTERN = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize(html: str) -> str:
    """Strip markup and whitespace so cosmetic rendering changes do not alarm."""

    text = SCRIPT_PATTERN.sub(" ", html)
    text = TAG_PATTERN.sub(" ", text)
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def fetch(url: str) -> tuple[str | None, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as error:
        return None, f"{type(error).__name__}: {error}"
    normalized = normalize(body)
    if len(normalized) < 200:
        return None, "response too short to fingerprint"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest(), None


def load_sources(root: Path) -> list[dict[str, Any]]:
    document = yaml.safe_load((root / "knowledge" / "monitored_sources.yaml").read_text("utf-8"))
    entries: list[dict[str, Any]] = []
    for provider, items in (document.get("sources") or {}).items():
        for item in items:
            entries.append({"provider": provider, **item})
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Check official billing documentation for changes.")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Record the current hashes after a human has reviewed the report.",
    )
    parser.add_argument("--report", type=Path, help="Write the Markdown report to this path.")
    args = parser.parse_args()

    root = find_repo_root(Path(__file__))
    baseline_path = root / "knowledge" / "sources_baseline.json"
    baseline = json.loads(baseline_path.read_text("utf-8"))
    recorded: dict[str, Any] = baseline.get("sources", {})

    changed: list[str] = []
    new: list[str] = []
    failed: list[str] = []
    unchanged = 0
    updated: dict[str, Any] = {}
    lines = ["# CloudCause documentation change report", ""]
    lines.append(f"Checked {datetime.now(tz=UTC).isoformat()} against {baseline_path.name}.")
    lines.append("")

    for source in load_sources(root):
        digest, error = fetch(source["url"])
        if digest is None:
            failed.append(source["id"])
            lines.append(f"- **unreachable** `{source['id']}` - {error} - {source['url']}")
            continue
        previous = recorded.get(source["id"], {}).get("sha256")
        updated[source["id"]] = {
            "provider": source["provider"],
            "url": source["url"],
            "watches": source.get("watches", []),
            "sha256": digest,
            "checked_at": datetime.now(tz=UTC).isoformat(),
        }
        if previous is None:
            new.append(source["id"])
            lines.append(f"- **new** `{source['id']}` - first fingerprint recorded - {source['url']}")
        elif previous != digest:
            changed.append(source["id"])
            lines.append(
                f"- **changed** `{source['id']}` (watches: {', '.join(source.get('watches', []))}) "
                f"- {source['url']}"
            )
        else:
            unchanged += 1

    lines.extend(
        [
            "",
            f"changed: {len(changed)} | new: {len(new)} | unchanged: {unchanged} | unreachable: {len(failed)}",
            "",
            "## What happens next",
            "",
            "A change here is a signal, not a decision. A human reviews the diff and decides",
            "whether it is editorial, regional, preview-only, a deprecation, a schema change,",
            "or a real billing-rule change. Only then do rules, adapters, fixtures, and tests",
            "change, in a reviewed pull request. This job never edits production rules.",
        ]
    )
    report = "\n".join(lines)
    print(report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report + "\n", encoding="utf-8")

    if args.update_baseline:
        baseline_path.write_text(
            json.dumps(
                {
                    "note": baseline.get("note", ""),
                    "generated_at": datetime.now(tz=UTC).isoformat(),
                    "sources": {**recorded, **updated},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nBaseline updated: {baseline_path}")
        return 0

    if failed and not (changed or new):
        return 1
    return 2 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
