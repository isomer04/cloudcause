"""Live provider adapters.

These exist so the adapter boundary is complete and so ``CLOUDCAUSE_DATA_MODE=live``
fails with a clear message instead of silently falling back to fixtures. Each
connector will be added one provider at a time, using read-only roles only:

* AWS: Cost Explorer / Data Exports, Resource Explorer, CloudWatch, CloudTrail
  lookup, Compute Optimizer.
* Azure: Cost Management Query, Resource Graph, Azure Monitor, Activity Log,
  Advisor.
* GCP: Cloud Billing BigQuery export, Cloud Asset Inventory, Cloud Monitoring,
  Cloud Audit Logs, Recommender.
"""

from __future__ import annotations

from collections.abc import Sequence

from cloudcause_contracts import (
    AuditEvent,
    CloudResource,
    CostRecord,
    DateRange,
    MetricSeries,
    Provider,
    Recommendation,
    SourceResult,
)

from .protocols import BaseDataProvider


class LiveModeNotConfiguredError(RuntimeError):
    """Raised when live provider data is requested before a connector exists."""


class _NotImplementedLiveProvider(BaseDataProvider):
    provider: Provider
    required_roles: tuple[str, ...] = ()

    def _fail(self, capability: str) -> None:
        raise LiveModeNotConfiguredError(
            f"{self.provider} live {capability} has no connector yet and is not implemented. "
            f"Run with CLOUDCAUSE_DATA_MODE=fixtures, or add the read-only connector "
            f"(required read-only access: {', '.join(self.required_roles) or 'see docs/architecture.md'})."
        )

    async def get_costs(self, periods: Sequence[DateRange]) -> SourceResult[CostRecord]:
        self._fail("cost data")
        raise AssertionError("unreachable")

    async def get_resources(self) -> SourceResult[CloudResource]:
        self._fail("inventory")
        raise AssertionError("unreachable")

    async def get_metrics(self, resource_ids: Sequence[str] | None = None) -> SourceResult[MetricSeries]:
        self._fail("metrics")
        raise AssertionError("unreachable")

    async def get_audit_events(self, periods: Sequence[DateRange]) -> SourceResult[AuditEvent]:
        self._fail("audit events")
        raise AssertionError("unreachable")

    async def get_recommendations(self) -> SourceResult[Recommendation]:
        self._fail("recommendations")
        raise AssertionError("unreachable")


class LiveAwsDataProvider(_NotImplementedLiveProvider):
    provider: Provider = "aws"
    required_roles = ("Cost Explorer read", "CloudWatch read", "CloudTrail lookup", "tagging read")


class LiveAzureDataProvider(_NotImplementedLiveProvider):
    provider: Provider = "azure"
    required_roles = ("Cost Management Reader", "Reader", "Monitoring Reader", "Advisor read")


class LiveGcpDataProvider(_NotImplementedLiveProvider):
    provider: Provider = "gcp"
    required_roles = (
        "Billing Account Viewer",
        "BigQuery Data Viewer",
        "Cloud Asset Viewer",
        "Recommender Viewer",
        "Logging Viewer",
    )
