"""Fail a local live evaluation before pytest when hosted-model keys are absent."""

from __future__ import annotations

import os

from cloudcause_contracts import find_repo_root, load_env_file


def main() -> None:
    values = {**load_env_file(find_repo_root() / ".env"), **os.environ}
    missing = [
        key
        for key in ("OPENAI_API_KEY", "GOOGLE_API_KEY")
        if not values.get(key) or values[key] == "replace-me"
    ]
    if missing:
        raise SystemExit("live evaluation requires " + ", ".join(missing))


if __name__ == "__main__":
    main()
