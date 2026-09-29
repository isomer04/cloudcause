"""Primitive types shared by every CloudCause service."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

Provider = Literal["aws", "azure", "gcp"]
PROVIDERS: tuple[Provider, ...] = ("aws", "azure", "gcp")

#: Where a number came from. ``upload`` is data a human handed CloudCause, which
#: is real but unverified; it must never be rendered as ``live``.
DataOrigin = Literal["fixture", "upload", "live"]
DATA_ORIGINS: tuple[DataOrigin, ...] = ("fixture", "upload", "live")

#: FOCUS release the MVP is pinned to. Unknown versions must fail safely.
SUPPORTED_FOCUS_VERSION = "1.4"

Risk = Literal["low", "medium", "high"]
SupportStatus = Literal["supported", "provisional", "deprecated", "unsupported"]


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class CloudCauseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def reconcile_origin(data: Any) -> Any:
    """Keep ``origin`` and the deprecated ``is_fixture`` flag in agreement.

    ``is_fixture`` predates :data:`DataOrigin` and cannot say "a human uploaded
    this". It stays a declared field rather than a computed one on purpose:
    ``CloudCauseModel`` forbids extra keys, pydantic serializes computed fields,
    and ``WorkerResponse.sources`` crosses HTTP, so a computed field would make
    serialize-then-validate fail with "extra inputs are not permitted".

    Whichever of the two a caller supplies, the other is derived, so old call
    sites and ``fixtures/*/manifest.json`` keep working while ``origin`` becomes
    the source of truth.
    """

    if not isinstance(data, dict):
        return data
    has_origin = data.get("origin") is not None
    has_flag = data.get("is_fixture") is not None
    if has_origin:
        # origin wins even when both are given: a payload claiming
        # origin="upload" with is_fixture=True would otherwise store a
        # contradiction and let uploaded numbers read as verified fixtures.
        return {**data, "is_fixture": data["origin"] == "fixture"}
    if has_flag:
        return {**data, "origin": "fixture" if data["is_fixture"] else "live"}
    return data


class DateRange(CloudCauseModel):
    """Inclusive date range: both ``start`` and ``end`` are billed days."""

    start: date
    end: date

    @model_validator(mode="after")
    def _check_order(self) -> DateRange:
        if self.end < self.start:
            raise ValueError("DateRange.end must not be before DateRange.start")
        return self

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end

    def dates(self) -> list[date]:
        return [self.start + timedelta(days=offset) for offset in range(self.days)]

    def label(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


class Provenance(CloudCauseModel):
    """Where a piece of data came from and how current it is.

    Every operational tool and fixture returns this next to its payload so the
    report can never present delayed data as real time coverage.

    ``origin`` is the source of truth. ``is_fixture`` is deprecated and kept only
    so the worker HTTP contract and the fixture manifests keep validating; it is
    derived from ``origin`` when it is not supplied.
    """

    provider: Provider
    source: str
    observed_at: datetime
    retrieved_at: datetime
    data_through: datetime
    origin: DataOrigin = "fixture"
    is_fixture: bool = True
    schema_version: str = "1"
    query_reference: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _origin_and_flag_agree(cls, data: Any) -> Any:
        return reconcile_origin(data)

    def reference(self) -> str:
        return self.query_reference or f"{self.provider}:{self.source}"


ItemT = TypeVar("ItemT")


class SourceResult(BaseModel, Generic[ItemT]):
    """A provider payload that always travels with its provenance."""

    model_config = ConfigDict(extra="forbid")

    provenance: Provenance
    items: list[ItemT] = Field(default_factory=list)

    def __len__(self) -> int:  # pragma: no cover - convenience
        return len(self.items)

    def __iter__(self):  # type: ignore[override]
        return iter(self.items)
