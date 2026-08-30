"""The provider adapter boundary.

Business logic and agents only ever see these interfaces, never a cloud SDK
client. Fixture and live adapters are interchangeable, so prompts and report
generation behave identically in both modes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from cloudcause_contracts import (
    AuditEvent,
    CloudResource,
    CostRecord,
    DateRange,
    MetricSeries,
    Provider,
    ProviderDataBundle,
    Recommendation,
    SourceResult,
)


@runtime_checkable
class CloudDataProvider(Protocol):
    """Read-only provider data access. No method may modify a cloud resource."""

    provider: Provider

    async def get_costs(self, periods: Sequence[DateRange]) -> SourceResult[CostRecord]: ...

    async def get_resources(self) -> SourceResult[CloudResource]: ...

    async def get_metrics(self, resource_ids: Sequence[str] | None = None) -> SourceResult[MetricSeries]: ...

    async def get_audit_events(self, periods: Sequence[DateRange]) -> SourceResult[AuditEvent]: ...

    async def get_recommendations(self) -> SourceResult[Recommendation]: ...


class BaseDataProvider(ABC):
    """Shared bundle assembly for every adapter."""

    provider: Provider

    @abstractmethod
    async def get_costs(self, periods: Sequence[DateRange]) -> SourceResult[CostRecord]: ...

    @abstractmethod
    async def get_resources(self) -> SourceResult[CloudResource]: ...

    @abstractmethod
    async def get_metrics(self, resource_ids: Sequence[str] | None = None) -> SourceResult[MetricSeries]: ...

    @abstractmethod
    async def get_audit_events(self, periods: Sequence[DateRange]) -> SourceResult[AuditEvent]: ...

    @abstractmethod
    async def get_recommendations(self) -> SourceResult[Recommendation]: ...

    async def get_bundle(self, periods: Sequence[DateRange]) -> ProviderDataBundle:
        return ProviderDataBundle(
            provider=self.provider,
            costs=await self.get_costs(periods),
            resources=await self.get_resources(),
            metrics=await self.get_metrics(),
            audit_events=await self.get_audit_events(periods),
            recommendations=await self.get_recommendations(),
        )
