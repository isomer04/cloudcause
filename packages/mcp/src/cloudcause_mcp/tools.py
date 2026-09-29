"""The evidence boundary, as plain async functions.

Both MCP servers wrap these. Every response carries provenance, every operation
is read-only, and nothing here can delete, stop, scale, or modify a resource or
policy. Tool results are data, never instructions.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from cloudcause_anomaly import allocate_actor_costs, group_changes, group_changes_by_actor
from cloudcause_contracts import (
    DIMENSIONS,
    DateRange,
    Dimension,
    Provenance,
    Provider,
    RuleQueryResult,
    Settings,
    get_settings,
)
from cloudcause_datasets import Dataset
from cloudcause_knowledge import KnowledgeStore, load_knowledge_store
from cloudcause_providers import UploadDataProvider, get_data_provider

READ_ONLY = True

#: Explicit allowlist. A tool name that is not in here is not exposed.
OPERATIONAL_TOOL_ALLOWLIST = (
    "get_cost_breakdown",
    "get_resource_inventory",
    "get_resource_metrics",
    "get_audit_events",
    "get_recommendations",
    "get_data_freshness",
)

KNOWLEDGE_TOOL_ALLOWLIST = (
    "get_billing_rule",
    "get_cost_driver_definitions",
    "get_provider_data_freshness_rules",
    "get_export_schema_version",
    "get_api_deprecation_status",
    "get_pricing_source",
    "get_known_billing_change",
)


def _provenance_dict(provenance: Provenance) -> dict[str, Any]:
    return {
        "provider": provenance.provider,
        "source": provenance.source,
        "observed_at": provenance.observed_at.isoformat(),
        "retrieved_at": provenance.retrieved_at.isoformat(),
        "data_through": provenance.data_through.isoformat(),
        "origin": provenance.origin,
        "is_fixture": provenance.is_fixture,
        "schema_version": provenance.schema_version,
        "query_reference": provenance.reference(),
    }


def _rule_result_dict(result: RuleQueryResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "found": result.found,
        "warnings": list(result.warnings),
        "requested": {
            "provider": result.requested_provider,
            "service": result.requested_service,
            "date": result.requested_date.isoformat() if result.requested_date else None,
        },
        "read_only": READ_ONLY,
    }
    if result.rule is not None:
        rule = result.rule
        payload["rule"] = {
            "id": rule.id,
            "provider": rule.provider,
            "rule_type": rule.rule_type,
            "title": rule.title,
            "service": rule.service,
            "summary": rule.summary,
            "cost_drivers": rule.cost_drivers,
            "investigation_checks": rule.investigation_checks,
            "schema_version": rule.schema_version,
            "valid_from": rule.valid_from.isoformat() if rule.valid_from else None,
            "valid_to": rule.valid_to.isoformat() if rule.valid_to else None,
            "reviewed_at": rule.reviewed_at.isoformat() if rule.reviewed_at else None,
            "confidence": rule.confidence,
            "source": {
                "type": rule.source.type,
                "url": rule.source.url,
                "updated_at": rule.source.updated_at.isoformat() if rule.source.updated_at else None,
            },
            "data": rule.data,
        }
    if result.citation is not None:
        payload["citation"] = result.citation.model_dump(mode="json")
    return payload


class OperationalDataTools:
    """Read-only provider operational data for one provider."""

    def __init__(
        self,
        provider: Provider,
        settings: Settings | None = None,
        scenario_id: str = "default",
        dataset_id: str | None = None,
        dataset: Dataset | None = None,
    ) -> None:
        self.provider: Provider = provider
        self.settings = settings or get_settings()
        self.scenario_id = scenario_id
        self.dataset_id = dataset_id
        self._adapter = (
            UploadDataProvider(provider, dataset)
            if dataset is not None
            else get_data_provider(provider, self.settings, scenario_id, dataset_id)
        )

    async def get_cost_breakdown(
        self,
        current_start: str,
        current_end: str,
        baseline_start: str,
        baseline_end: str,
        group_by: str = "service",
        tag_key: str | None = None,
    ) -> dict[str, Any]:
        """Grouped cost change between two periods, computed deterministically.

        ``group_by="tag_owner"`` accepts a ``tag_key`` to group by any activated
        cost allocation tag; without one it uses the default owner keys.

        ``group_by="actor"`` is the one grouping the bill cannot prove. It joins
        cost against audit evidence and apportions it, so the response carries
        ``attribution`` - the weighting method, the unattributed share, and the
        warnings behind them - and the caller is expected to report it as an
        estimate. See ADR 0013.
        """

        current = DateRange(start=date.fromisoformat(current_start), end=date.fromisoformat(current_end))
        baseline = DateRange(start=date.fromisoformat(baseline_start), end=date.fromisoformat(baseline_end))
        result = await self._adapter.get_costs([current, baseline])
        # Derived from Dimension rather than repeated, so a new dimension cannot
        # be added to the contract and silently stay unreachable here.
        dimension: Dimension = group_by if group_by in DIMENSIONS else "service"  # type: ignore[assignment]
        attribution: dict[str, Any] | None = None
        if dimension == "actor":
            events = await self._adapter.get_audit_events([current, baseline])
            changes, allocation = group_changes_by_actor(
                result.items, events.items, current, baseline, self.provider
            )
            current_allocation = allocate_actor_costs(
                [record for record in result.items if current.contains(record.usage_date)],
                [event for event in events.items if current.contains(event.event_time.astimezone(UTC).date())],
            )
            baseline_allocation = allocate_actor_costs(
                [record for record in result.items if baseline.contains(record.usage_date)],
                [event for event in events.items if baseline.contains(event.event_time.astimezone(UTC).date())],
            )
            attribution = {
                "basis": "audit_join_estimate",
                "weight_method": allocation.weight_method,
                "attributed_cost": allocation.attributed_cost,
                "unattributed_cost": allocation.unattributed_cost,
                "unattributed_share": allocation.unattributed_share,
                "periods": {
                    "current": {
                        "weight_method": current_allocation.weight_method,
                        "attributed_cost": current_allocation.attributed_cost,
                        "unattributed_cost": current_allocation.unattributed_cost,
                        "unattributed_share": current_allocation.unattributed_share,
                    },
                    "baseline": {
                        "weight_method": baseline_allocation.weight_method,
                        "attributed_cost": baseline_allocation.attributed_cost,
                        "unattributed_cost": baseline_allocation.unattributed_cost,
                        "unattributed_share": baseline_allocation.unattributed_share,
                    },
                },
                "audit_provenance": _provenance_dict(events.provenance),
                "warnings": list(allocation.warnings),
                "note": (
                    "No billing export names the principal that made a call, so these shares are "
                    "an estimate produced by joining cost to audit evidence, not a billed fact."
                ),
            }
        else:
            changes = group_changes(
                result.items, dimension, current, baseline, self.provider, tag_key=tag_key
            )
        serialized_groups = [
            {
                "key": change.key,
                "service_name": change.service_name,
                "region_id": change.region_id,
                "resource_id": change.resource_id,
                "baseline_cost": change.baseline_cost,
                "expected_baseline_cost": change.expected_baseline_cost,
                "current_cost": change.current_cost,
                "absolute_change": change.absolute_change,
                "percent_change": change.percent_change,
                "quantity_percent_change": change.quantity_percent_change,
                "currency": change.currency,
            }
            for change in changes
        ]
        payload: dict[str, Any] = {
            "provenance": _provenance_dict(result.provenance),
            "group_by": dimension,
            "tag_key": tag_key if dimension == "tag_owner" else None,
            "current_period": current.label(),
            "baseline_period": baseline.label(),
            "groups": serialized_groups[:25],
            "group_page": {
                "returned": min(len(serialized_groups), 25),
                "total": len(serialized_groups),
                "omitted": max(len(serialized_groups) - 25, 0),
                "omitted_current_cost": round(
                    sum(group["current_cost"] for group in serialized_groups[25:]), 6
                ),
                "omitted_baseline_cost": round(
                    sum(group["baseline_cost"] for group in serialized_groups[25:]), 6
                ),
            },
            "read_only": READ_ONLY,
        }
        if attribution is not None:
            payload["attribution"] = attribution
        return payload

    async def get_resource_inventory(
        self, resource_id: str | None = None, resource_type: str | None = None
    ) -> dict[str, Any]:
        """Inventory records, optionally filtered by resource id or type."""

        result = await self._adapter.get_resources()
        items = result.items
        if resource_id:
            items = [item for item in items if item.resource_id == resource_id]
        if resource_type:
            lowered = resource_type.lower()
            items = [item for item in items if lowered in item.resource_type.lower()]
        return {
            "provenance": _provenance_dict(result.provenance),
            "items": [item.model_dump(mode="json") for item in items[:100]],
            "read_only": READ_ONLY,
        }

    async def get_resource_metrics(self, resource_id: str) -> dict[str, Any]:
        """Metric series for one resource."""

        result = await self._adapter.get_metrics([resource_id])
        return {
            "provenance": _provenance_dict(result.provenance),
            "items": [item.model_dump(mode="json") for item in result.items],
            "read_only": READ_ONLY,
        }

    async def get_audit_events(self, start: str, end: str, resource_id: str | None = None) -> dict[str, Any]:
        """Control-plane events in a window. Summaries are untrusted text."""

        period = DateRange(start=date.fromisoformat(start), end=date.fromisoformat(end))
        result = await self._adapter.get_audit_events([period])
        items = result.items
        if resource_id:
            items = [event for event in items if resource_id in event.resource_ids]
        return {
            "provenance": _provenance_dict(result.provenance),
            "items": [event.model_dump(mode="json") for event in items[:100]],
            "untrusted_content": True,
            "read_only": READ_ONLY,
        }

    async def get_recommendations(self) -> dict[str, Any]:
        """Provider cost recommendations. Advisory only, never applied."""

        result = await self._adapter.get_recommendations()
        return {
            "provenance": _provenance_dict(result.provenance),
            "items": [item.model_dump(mode="json") for item in result.items],
            "read_only": READ_ONLY,
        }

    async def get_data_freshness(self) -> dict[str, Any]:
        """How current each source is, so delayed data is never read as zero usage."""

        periods: list[DateRange] = []
        if self.settings.data_mode == "live":
            yesterday = datetime.now(tz=UTC).date() - timedelta(days=1)
            lookback = min(7, self.settings.live_query_max_days)
            periods = [DateRange(start=yesterday - timedelta(days=lookback - 1), end=yesterday)]
        costs = await self._adapter.get_costs(periods)
        resources = await self._adapter.get_resources()
        return {
            "provider": self.provider,
            "sources": {
                "costs": _provenance_dict(costs.provenance),
                "resources": _provenance_dict(resources.provenance),
            },
            "read_only": READ_ONLY,
        }


class BillingKnowledgeTools:
    """Read-only billing knowledge lookups, selected by usage date."""

    def __init__(self, settings: Settings | None = None, store: KnowledgeStore | None = None) -> None:
        self.settings = settings or get_settings()
        self.store = store or load_knowledge_store(
            self.settings.knowledge_root,
            review_max_age_days=self.settings.knowledge_review_max_age_days,
            focus_version=self.settings.focus_version,
        )

    def get_billing_rule(
        self,
        provider: str,
        service: str | None = None,
        category: str | None = None,
        usage_date: str | None = None,
        rule_type: str = "cost_driver",
    ) -> dict[str, Any]:
        """The rule that explains how a charge is billed, effective on ``usage_date``."""

        return _rule_result_dict(
            self.store.get_billing_rule(
                provider,  # type: ignore[arg-type]
                service=service,
                category=category,
                usage_date=date.fromisoformat(usage_date) if usage_date else None,
                rule_type=rule_type,  # type: ignore[arg-type]
            )
        )

    def get_cost_driver_definitions(
        self,
        provider: str,
        service: str | None = None,
        category: str | None = None,
        usage_date: str | None = None,
    ) -> dict[str, Any]:
        """What actually drives cost for a service, with the checks to run."""

        return _rule_result_dict(
            self.store.get_cost_driver_definitions(
                provider,  # type: ignore[arg-type]
                service=service,
                category=category,
                usage_date=date.fromisoformat(usage_date) if usage_date else None,
            )
        )

    def get_provider_data_freshness_rules(self, provider: str, usage_date: str | None = None) -> dict[str, Any]:
        """Documented billing data delay for a provider."""

        return _rule_result_dict(
            self.store.get_provider_data_freshness_rules(
                provider,  # type: ignore[arg-type]
                usage_date=date.fromisoformat(usage_date) if usage_date else None,
            )
        )

    def get_export_schema_version(self, provider: str, usage_date: str | None = None) -> dict[str, Any]:
        """Which export schema version CloudCause supports for a provider."""

        return _rule_result_dict(
            self.store.get_export_schema_version(
                provider,  # type: ignore[arg-type]
                usage_date=date.fromisoformat(usage_date) if usage_date else None,
            )
        )

    def get_api_deprecation_status(self, provider: str, api: str | None = None) -> dict[str, Any]:
        """Whether a billing API is current, superseded, or retired."""

        return _rule_result_dict(
            self.store.get_api_deprecation_status(provider, api=api)  # type: ignore[arg-type]
        )

    def get_pricing_source(self, provider: str, service: str | None = None) -> dict[str, Any]:
        """The only acceptable price source for a provider."""

        return _rule_result_dict(
            self.store.get_pricing_source(provider, service=service)  # type: ignore[arg-type]
        )

    def get_known_billing_change(
        self, provider: str, start: str | None = None, end: str | None = None
    ) -> dict[str, Any]:
        """Documented billing behaviour changes that overlap a period."""

        rules = self.store.get_known_billing_change(
            provider,  # type: ignore[arg-type]
            start=date.fromisoformat(start) if start else None,
            end=date.fromisoformat(end) if end else None,
        )
        return {
            "found": bool(rules),
            "changes": [
                {
                    "id": rule.id,
                    "title": rule.title,
                    "valid_from": rule.valid_from.isoformat() if rule.valid_from else None,
                    "summary": rule.summary,
                    "source_url": rule.source.url,
                    "reviewed_at": rule.reviewed_at.isoformat() if rule.reviewed_at else None,
                    "data": rule.data,
                }
                for rule in rules
            ],
            "read_only": READ_ONLY,
        }
