"""Evidence validation: the gate between agent output and the final report."""

from .validation import (
    CAUSE_SUPPORTING_SOURCES,
    FALLBACK_CATEGORY,
    UNSUPPORTED_CAUSE_MAX_CONFIDENCE,
    ValidationResult,
    missing_cause_sources,
    rank_findings,
    validate_findings,
)

__all__ = [
    "CAUSE_SUPPORTING_SOURCES",
    "FALLBACK_CATEGORY",
    "UNSUPPORTED_CAUSE_MAX_CONFIDENCE",
    "ValidationResult",
    "missing_cause_sources",
    "rank_findings",
    "validate_findings",
]
