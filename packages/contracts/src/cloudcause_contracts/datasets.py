"""Uploaded dataset contracts.

A dataset is how a stranger's own cost export reaches the same engine the demo
fixtures use. It is created empty, filled one source at a time, then sealed;
after sealing it is immutable, which is what makes it safe for the gateway, the
orchestrator, both workers, and every MCP child process to read concurrently
while an investigation runs.

Nothing raw is stored. What travels through these models is a *summary* of what
was accepted: counts, detected formats, the period actually covered, and the
freshness boundary. Row values never appear in an ingest report, and neither does
a filename, because a source is addressed by ``{provider}/{kind}`` in the URL.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from .common import CloudCauseModel, DateRange, Provider, utcnow
from .investigation import InvestigationRequest

#: The five sources a dataset may carry per provider. ``cost`` is required;
#: the other four are the Tier 2 evidence that lets a cause be named.
DatasetSourceKind = Literal["cost", "metrics", "audit", "inventory", "recommendations"]
DATASET_SOURCE_KINDS: tuple[DatasetSourceKind, ...] = (
    "cost",
    "metrics",
    "audit",
    "inventory",
    "recommendations",
)

#: ``Evidence.source_type`` values each dataset source kind can support.
SOURCE_KIND_EVIDENCE_TYPES: dict[DatasetSourceKind, tuple[str, ...]] = {
    "cost": ("cost", "usage"),
    "metrics": ("metric",),
    "audit": ("audit",),
    "inventory": ("inventory",),
    "recommendations": ("recommendation",),
}

#: What the browser is allowed to send. Anything else is refused before parsing.
ACCEPTED_CONTENT_TYPES: tuple[str, ...] = (
    "application/json",
    "text/csv",
    "application/gzip",
)


class DatasetRowRejection(CloudCauseModel):
    """One row that could not be used, named by position and reason only.

    ``detail`` names columns and row numbers. It never quotes a row value, so an
    error message cannot leak somebody's billing data back to them through a log.
    """

    row_number: int
    code: str
    detail: str


class DatasetSourceSummary(CloudCauseModel):
    """What one accepted upload contributed to a dataset."""

    provider: Provider
    kind: DatasetSourceKind
    detected_format: str
    received_at: datetime = Field(default_factory=utcnow)
    raw_rows: int = 0
    accepted_rows: int = 0
    rejected_rows: int = 0
    stored_records: int = 0
    period_start: date | None = None
    period_end: date | None = None
    data_through: datetime | None = None
    data_through_note: str = ""
    currency: str | None = None
    byte_size: int = 0
    compressed: bool = False

    @property
    def period(self) -> DateRange | None:
        if self.period_start is None or self.period_end is None:
            return None
        return DateRange(start=self.period_start, end=self.period_end)


class DatasetIngestReport(CloudCauseModel):
    """The answer to one ``PUT .../sources/{provider}/{kind}``."""

    dataset_id: str
    expires_at: datetime
    sealed: bool = False
    source: DatasetSourceSummary
    rejections: list[DatasetRowRejection] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    total_records: int = 0
    source_count: int = 0


class DatasetSummary(CloudCauseModel):
    """The whole dataset as the UI sees it. Never carries a row."""

    dataset_id: str
    created_at: datetime
    expires_at: datetime
    sealed: bool = False
    sealed_at: datetime | None = None
    providers: list[Provider] = Field(default_factory=list)
    sources: list[DatasetSourceSummary] = Field(default_factory=list)
    currency: str | None = None
    total_records: int = 0
    period_start: date | None = None
    period_end: date | None = None
    data_through: datetime | None = None
    #: Per provider, the ``Evidence.source_type`` values this dataset can back.
    available_source_types: dict[Provider, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    #: A ready-to-run brief over the period the data actually covers, so the UI
    #: never has to derive dates of its own.
    suggested_request: InvestigationRequest | None = None

    def source(self, provider: Provider, kind: DatasetSourceKind) -> DatasetSourceSummary | None:
        for entry in self.sources:
            if entry.provider == provider and entry.kind == kind:
                return entry
        return None

    def has_cost_data(self) -> bool:
        return any(entry.kind == "cost" and entry.stored_records for entry in self.sources)


class DatasetCreated(CloudCauseModel):
    """The answer to ``POST /api/v1/datasets``."""

    dataset_id: str
    created_at: datetime
    expires_at: datetime
    max_bytes_per_file: int
    max_rows_per_file: int
    max_sources: int
    max_records: int
    accepted_content_types: list[str] = Field(default_factory=lambda: list(ACCEPTED_CONTENT_TYPES))
    source_kinds: list[str] = Field(default_factory=lambda: list(DATASET_SOURCE_KINDS))
