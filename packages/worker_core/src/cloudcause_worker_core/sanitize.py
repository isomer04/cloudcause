"""Untrusted text handling.

Resource names, tags, labels, audit log summaries, and recommendation text are
attacker-influenceable. They are data, never instructions: CloudCause truncates
them, strips control characters, and flags prompt-injection shapes so a finding
that quotes them can be downgraded.
"""

from __future__ import annotations

import re
import unicodedata

MAX_LENGTH = 400

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore\s+(all\s+)?(previous|prior|above)",
        r"disregard\s+(all\s+)?(previous|prior|the)",
        r"you\s+are\s+now\b",
        r"\bact\s+as\b",
        r"system\s*prompt",
        r"\b(system|assistant|developer)\s*:",
        r"</?\s*(script|iframe|img)\b",
        r"```",
        r"\btool_call\b",
        r"\bexecute\s+(the\s+)?following\b",
        r"\b(delete|terminate|shutdown)\s+(all|every)\b",
    )
)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def looks_like_injection(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def scrub(text: str | None, max_length: int = MAX_LENGTH) -> tuple[str, bool]:
    """Return ``(safe_text, suspicious)`` for one piece of untrusted text."""

    if not text:
        return "", False
    normalized = unicodedata.normalize("NFKC", str(text))
    normalized = _CONTROL_CHARS.sub(" ", normalized)
    normalized = normalized.replace("\r", " ").replace("\n", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    suspicious = looks_like_injection(normalized)
    if suspicious:
        normalized = f"[flagged untrusted text] {normalized}"
    if len(normalized) > max_length:
        normalized = normalized[: max_length - 3].rstrip() + "..."
    return normalized, suspicious


def scrub_tags(tags: dict[str, str], limit: int = 6) -> tuple[str, bool]:
    """Render a tag map as a short, safe string."""

    if not tags:
        return "no tags", False
    parts: list[str] = []
    suspicious = False
    for key, value in list(tags.items())[:limit]:
        safe_key, key_flag = scrub(key, 40)
        safe_value, value_flag = scrub(value, 60)
        suspicious = suspicious or key_flag or value_flag
        parts.append(f"{safe_key}={safe_value}")
    return ", ".join(parts), suspicious
