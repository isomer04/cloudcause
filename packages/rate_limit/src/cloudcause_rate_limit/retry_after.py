"""Parse an HTTP ``Retry-After`` header value.

Shared by the live model-call retry policy and the live cost connectors, so the
two agree on what a usable delay is.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import isfinite


def parse_retry_after(raw: str | None) -> float | None:
    """Seconds to wait, or ``None`` when the value is absent or unusable.

    RFC 9110 allows either a delay in seconds or an HTTP-date. A negative delay
    is not a delay; a date already in the past means "retry now".
    """

    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return _http_date_delay(raw)
    return seconds if isfinite(seconds) and seconds >= 0 else None


def _http_date_delay(raw: str) -> float | None:
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())
