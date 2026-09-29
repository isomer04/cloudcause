"""Local-only live cost connectors (ADR 0015).

These adapters read the operator's *own* bill through the credential their cloud
CLI already holds - ``az login``, an AWS profile or SSO session, ``gcloud auth
application-default login``. CloudCause holds no credential of its own: the
settings carry identifiers (a subscription id, a BigQuery table name) and nothing
else, and the deployed service stays pinned to ``CLOUDCAUSE_DATA_MODE=fixtures``
in Terraform. ADR 0014's refusal - customer credentials, a deployed connector,
secret storage - is untouched.

Every connector is two pieces:

* a **fetch** that imports its SDK lazily, resolves the ambient credential, and
  returns the raw wire shape. It is opt-in (``pytest -m cloud``) and never runs
  in CI;
* a pure **mapper** from that raw shape to ``list[CostRecord]``, tested offline
  against a recorded response in ``tests/data/live_responses/``. That is what
  answers ADR 0014's maintenance objection to a local-only connector.

Only ``get_costs`` is live. The other four sources come back *absent* - an empty
result whose provenance says so - exactly as a cost-only upload does, so a live
run degrades to ``unexplained_increase`` under ADR 0006 rather than failing.
``get_bundle`` calls all five, which is why they must not raise.

Rules that hold everywhere in this module:

* A credential never enters a message, a log line, or an exception chain. SDK
  errors are wrapped by :func:`connector_error`, which keeps the class name and
  an HTTP status and drops the message.
* Money is deterministic. Nothing here rounds, adjusts, or estimates a figure;
  the mappers copy what the provider returned.
* Every call is read-only.
* The current UTC day is never requested. Each provider's own freshness signal
  sets ``data_through``; the worker turns that into the delayed-data warning.
* One investigation fetches once. The orchestrator, the worker and the MCP cost
  tool each ask the adapter for costs, so the raw response is cached per process
  for :data:`LIVE_FETCH_TTL_SECONDS`, keyed by provider, identifier and window.
* Cloud SDKs log request detail at DEBUG. Their loggers are raised to INFO the
  first time a live fetch runs, so a developer's DEBUG log level cannot turn an
  SDK into a leak (ADR 0016).
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import re
import time as clock
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, TypeVar
from urllib.parse import urlparse
from uuid import UUID

import httpx
from cloudcause_contracts import (
    AuditEvent,
    CloudResource,
    CostRecord,
    DateRange,
    MetricSeries,
    Provenance,
    Provider,
    Recommendation,
    Settings,
    SourceResult,
    utcnow,
)
from cloudcause_datasets import azure_data_through as upload_azure_data_through
from cloudcause_datasets import end_of_day, gcp_data_through
from cloudcause_focus import (
    as_float,
    aws_charge_category,
    parse_azure_cost_management,
    parse_gcp_billing_export,
    service_category,
)
from cloudcause_rate_limit import parse_retry_after

from .protocols import BaseDataProvider

log = logging.getLogger(__name__)

ItemT = TypeVar("ItemT")

#: Which optional extra each provider's fetch needs. Imported inside the fetch,
#: never at module scope: this module is imported on every fixture run.
LIVE_EXTRAS: dict[Provider, tuple[str, str]] = {
    "aws": ("boto3", "aws"),
    "azure": ("azure.identity", "azure"),
    "gcp": ("google.cloud.bigquery", "gcp"),
}

AZURE_API_VERSION = "2023-11-01"
AZURE_MANAGEMENT_SCOPE = "https://management.azure.com/.default"
AZURE_QUERY_URL = (
    "https://management.azure.com/subscriptions/{subscription}/providers/"
    "Microsoft.CostManagement/query?api-version=" + AZURE_API_VERSION
)
#: The Query API allows two group-by clauses. These two are what
#: ``resource_key()`` and ``service_category()`` key on; ChargeType, Meter and
#: ResourceLocation are lost and the docstring on the Azure adapter says so.
AZURE_GROUPINGS = ("ResourceId", "MeterCategory")
AZURE_MAX_ATTEMPTS = 4
AZURE_MAX_PAGES = 100
AZURE_RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}
#: The longest single wait a ``Retry-After`` header can impose. A larger value
#: fails the page rather than stalling an investigation for minutes.
AZURE_MAX_RETRY_WAIT_SECONDS = 60.0

AWS_COST_EXPLORER_REGION = "us-east-1"
AWS_METRICS = ("UnblendedCost", "AmortizedCost", "UsageQuantity")
AWS_GROUP_BY = ("SERVICE", "USAGE_TYPE")

#: A BigQuery table reference is the one string that has to be interpolated into
#: SQL (table names cannot be query parameters), so it is proven safe first.
#: Both the standard and the detailed (``_resource``) export are accepted; only
#: the detailed one has the ``resource`` column, and the query adapts to that.
GCP_TABLE_PATTERN = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]\.[A-Za-z0-9_]{1,1024}\."
    r"gcp_billing_export(_resource)?_v1_[0-9A-Z_]+$"
)
#: Usage for a day can land in a partition several days later.
GCP_PARTITION_LAG_DAYS = 7
#: The export's dotted column names, as the parser expects them, keyed by the
#: underscore alias the query has to use (BigQuery aliases cannot contain dots).
GCP_COLUMN_ALIASES = {
    "service_description": "service.description",
    "sku_id": "sku.id",
    "sku_description": "sku.description",
    "location_location": "location.location",
    "resource_name": "resource.name",
    "resource_global_name": "resource.global_name",
    "usage_unit": "usage.unit",
    "usage_amount": "usage.amount",
    "credits_amount": "credits.amount",
}

#: How long one raw response is reused. Cost data is daily, an investigation
#: takes minutes, and the orchestrator, worker and MCP tool all ask within that.
LIVE_FETCH_TTL_SECONDS = 900.0
LIVE_FETCH_MAX_ENTRIES = 128

#: Loggers the cloud SDKs write request detail to at DEBUG. Raised to INFO on
#: the first live fetch, and never lowered again by this module.
SDK_LOGGERS = ("boto3", "botocore", "urllib3", "azure", "msal", "httpx", "httpcore", "google")

_fetch_cache: dict[tuple[str, str, str], tuple[float, Any]] = {}
_fetch_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
_sdk_loggers_muted = False

ABSENT_KINDS = {
    "inventory": CloudResource,
    "metrics": MetricSeries,
    "audit": AuditEvent,
    "recommendations": Recommendation,
}


class LiveModeNotConfiguredError(RuntimeError):
    """Live mode was requested but this machine cannot serve it.

    The message names what is missing - an extra, an identifier setting, a CLI
    login - and never what was found.
    """


class LiveConnectorError(RuntimeError):
    """A cloud call failed. Carries the exception class and status, never its text."""


def connector_error(provider: str, error: BaseException) -> LiveConnectorError:
    """Wrap an SDK failure so nothing from the provider's message survives.

    SDK messages echo request URLs, ARNs, tenant ids and SQL. None of that is a
    credential, but the rule this repo applies to uploads is "name the shape,
    never the value", and the only way to make that hold for a dozen SDK error
    types is to keep the class name and drop the rest.
    """

    status = _http_status(error)
    summary = type(error).__name__ + (f" (HTTP {status})" if status else "")
    log.warning("%s live connector failed: %s", provider, summary)
    return LiveConnectorError(f"{provider} live cost query failed: {summary}")


def _http_status(error: BaseException) -> int | None:
    for name in ("status_code", "status", "code"):
        value = getattr(error, name, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    response = getattr(error, "response", None)
    if isinstance(response, Mapping):  # botocore ClientError
        value = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        if isinstance(value, int):
            return value
    value = getattr(response, "status_code", None)  # httpx
    return value if isinstance(value, int) else None


def load_extra(provider: Provider) -> Any:
    """Import the provider's SDK, or say which extra to install."""

    module_name, extra = LIVE_EXTRAS[provider]
    try:
        return importlib.import_module(module_name)
    except ImportError as error:
        raise LiveModeNotConfiguredError(
            f"{provider} live mode needs the '{extra}' extra: run `uv sync --extra {extra}` "
            f"(module {module_name} is not installed)"
        ) from error


def clear_live_cache() -> None:
    """Forget every cached response. Tests call it; nothing else needs to."""

    _fetch_cache.clear()
    _fetch_locks.clear()


def mute_sdk_debug_logging() -> None:
    """Raise the cloud SDK loggers to INFO, once, before the first live call.

    botocore logs request parameters and partial signatures at DEBUG, azure-core
    logs request URLs, google-auth logs token refreshes. None of that belongs in
    a log line, and this module cannot scrub what it does not emit.

    The *effective* level decides. A logger that inherits WARNING from the root
    is already quiet, and pinning it to INFO would switch on request lines that
    were off - httpx logs every URL at INFO. Only a logger that would emit DEBUG
    is raised, and a developer's stricter setting is left alone.
    """

    global _sdk_loggers_muted
    if _sdk_loggers_muted:
        return
    for name in SDK_LOGGERS:
        logger = logging.getLogger(name)
        if logger.getEffectiveLevel() < logging.INFO:
            logger.setLevel(logging.INFO)
    _sdk_loggers_muted = True


def _yesterday(today: date | None = None) -> date:
    return (today or datetime.now(tz=UTC).date()) - timedelta(days=1)


def live_window(periods: Sequence[DateRange], today: date | None = None) -> DateRange | None:
    """The union of the requested periods, clipped so today is never requested.

    Today is always partial on every provider, and requesting it would either
    understate it or force a guess. ``None`` means nothing in the request is in
    the past yet.
    """

    if not periods:
        return None
    start = min(period.start for period in periods)
    end = min(max(period.end for period in periods), _yesterday(today))
    if end < start:
        return None
    return DateRange(start=start, end=end)


def _in_periods(record: CostRecord, periods: Sequence[DateRange]) -> bool:
    return any(period.contains(record.usage_date) for period in periods)


class _LocalLiveProvider(BaseDataProvider):
    """Shared shape: live costs, absent everything else."""

    provider: Provider
    source: str
    required_roles: tuple[str, ...] = ()

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # The boundary the last cost query established, so the absent sources
        # report the same coverage as the costs they sit next to.
        self._boundary: datetime | None = None

    async def _fetch(self, window: DateRange) -> Any:  # pragma: no cover - per provider
        raise NotImplementedError

    def _cache_scope(self) -> str:
        """What, besides provider and window, decides that two fetches are the same."""

        return ""

    async def _fetch_cached(self, window: DateRange) -> Any:
        """One fetch per provider, scope and window per process, for the TTL.

        A lock per key means two callers arriving together share one call rather
        than both paying for it. The cache holds only the operator's own data in
        the operator's own process, so it needs no tenancy. Entries are expired
        eagerly and capped so unique windows cannot grow process memory forever.
        """

        now = clock.monotonic()
        expired = [key for key, (expires_at, _raw) in _fetch_cache.items() if expires_at <= now]
        for expired_key in expired:
            _fetch_cache.pop(expired_key, None)
            _fetch_locks.pop(expired_key, None)
        if len(_fetch_cache) >= LIVE_FETCH_MAX_ENTRIES:
            oldest = min(_fetch_cache, key=lambda cache_key: _fetch_cache[cache_key][0])
            _fetch_cache.pop(oldest, None)
            _fetch_locks.pop(oldest, None)

        key = (self.provider, self._cache_scope(), window.label())
        lock = _fetch_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = _fetch_cache.get(key)
            now = clock.monotonic()
            if cached is not None and cached[0] > now:
                log.info("%s live costs: reusing the response fetched for %s", self.provider, window.label())
                return cached[1]
            mute_sdk_debug_logging()
            raw = await self._fetch(window)
            _fetch_cache[key] = (now + LIVE_FETCH_TTL_SECONDS, raw)
            return raw

    def _map(self, raw: Any, window: DateRange) -> tuple[list[CostRecord], datetime]:  # pragma: no cover
        raise NotImplementedError

    async def get_costs(self, periods: Sequence[DateRange]) -> SourceResult[CostRecord]:
        window = live_window(periods)
        if window is None:
            return self._cost_result([], end_of_day(_yesterday()), reference="nothing-in-the-past")
        window_days = (window.end - window.start).days + 1
        if window_days > self.settings.live_query_max_days:
            raise LiveModeNotConfiguredError(
                f"live cost queries are limited to {self.settings.live_query_max_days} days; "
                f"requested {window_days}"
            )
        try:
            raw = await self._fetch_cached(window)
        except (LiveModeNotConfiguredError, LiveConnectorError):
            raise
        except Exception as error:  # noqa: BLE001 - every SDK error is wrapped, on purpose
            # ``from None``: a chained cause would carry the SDK's message into
            # any traceback that gets logged.
            raise connector_error(self.provider, error) from None
        records, boundary = self._map(raw, window)
        selected = [
            record
            for record in records
            if record.usage_date <= boundary.date() and _in_periods(record, periods)
        ]
        log.info("%s live costs: %d records, complete through %s", self.provider, len(selected), boundary.date())
        return self._cost_result(selected, boundary, reference=window.label())

    def _provenance(self, source: str, boundary: datetime, query_reference: str) -> Provenance:
        """The one provenance shape every live source reports, costs or absent."""

        return Provenance(
            provider=self.provider,
            source=source,
            observed_at=boundary,
            retrieved_at=max(utcnow(), boundary),
            data_through=boundary,
            origin="live",
            schema_version="1",
            query_reference=query_reference,
        )

    def _cost_result(self, records: list[CostRecord], boundary: datetime, reference: str) -> SourceResult[CostRecord]:
        self._boundary = boundary
        provenance = self._provenance(self.source, boundary, f"live:{self.provider}/{self.source}#{reference}")
        return SourceResult[CostRecord](provenance=provenance, items=records)

    def _absent(self, kind: str) -> SourceResult[Any]:
        """An empty result that says "this source was not read", never "empty"."""

        boundary = self._boundary or end_of_day(_yesterday())
        provenance = self._provenance(
            f"{self.provider}-{kind}-absent", boundary, f"live:{self.provider}/{kind}#not-supplied"
        )
        return SourceResult[ABSENT_KINDS[kind]](provenance=provenance, items=[])  # type: ignore[valid-type]

    async def get_resources(self) -> SourceResult[CloudResource]:
        return self._absent("inventory")

    async def get_metrics(self, resource_ids: Sequence[str] | None = None) -> SourceResult[MetricSeries]:
        return self._absent("metrics")

    async def get_audit_events(self, periods: Sequence[DateRange]) -> SourceResult[AuditEvent]:
        return self._absent("audit")

    async def get_recommendations(self) -> SourceResult[Recommendation]:
        return self._absent("recommendations")


# ---------------------------------------------------------------------------
# Azure - Cost Management Query API, parsed by the upload parser
# ---------------------------------------------------------------------------


def azure_query_body(window: DateRange) -> dict[str, Any]:
    """The Query API request. Pure, so the recorded request can be asserted."""

    return {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {
            "from": datetime.combine(window.start, time(0, 0), tzinfo=UTC).isoformat().replace("+00:00", "Z"),
            "to": end_of_day(window.end).isoformat().replace("+00:00", "Z"),
        },
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": name} for name in AZURE_GROUPINGS],
        },
    }


def merge_azure_pages(pages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Concatenate ``rows`` across ``nextLink`` pages into one document.

    ``source_record_id`` is the row index, so parsing page by page would mint
    duplicate ids. Columns come from the first page; every page carries the same.
    """

    if not pages:
        return {"properties": {"columns": [], "rows": []}}
    first = pages[0].get("properties", pages[0])
    rows: list[Any] = []
    for page in pages:
        rows.extend(page.get("properties", page).get("rows", []))
    return {"properties": {"columns": list(first.get("columns", [])), "rows": rows}}


def map_azure_query_response(document: Mapping[str, Any], subscription_id: str) -> list[CostRecord]:
    """The Azure mapper: add the subscription column the response lacks, then parse.

    The Query API is scoped to a subscription and does not echo it, and the
    parser reads ``SubscriptionId`` for ``billing_account_id``. Injecting the
    column keeps the parser untouched and the recorded response verbatim.
    """

    properties = document.get("properties", document)
    columns = list(properties.get("columns", []))
    names = {str(column.get("name")) for column in columns}
    rows = [list(row) for row in properties.get("rows", [])]
    if "SubscriptionId" not in names:
        columns.append({"name": "SubscriptionId", "type": "String"})
        rows = [row + [subscription_id] for row in rows]
    return parse_azure_cost_management({"properties": {"columns": columns, "rows": rows}})


def azure_data_through(records: Sequence[CostRecord], window: DateRange) -> datetime:
    """Daily grain has no intraday signal, but a connector has a clock.

    The window never includes today, so the last day with any row is complete as
    far as Azure has reported. If the tail of the window came back empty, that
    is Azure's lag and the boundary says so rather than claiming the window.
    """

    if not records:
        return end_of_day(window.start - timedelta(days=1))
    boundary, _note = upload_azure_data_through(records)
    return boundary


def _validated_azure_page_url(url: str, subscription: str) -> str:
    parsed = urlparse(url)
    expected_prefix = f"/subscriptions/{subscription}/providers/Microsoft.CostManagement/query"
    if (
        parsed.scheme != "https"
        or parsed.hostname != "management.azure.com"
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.path.casefold() != expected_prefix.casefold()
    ):
        raise LiveConnectorError("azure live cost query returned an invalid nextLink")
    return url


class LiveAzureDataProvider(_LocalLiveProvider):
    """Azure costs via the Cost Management Query API.

    Credential: ``AzureCliCredential`` only - ``az login``. Not
    ``DefaultAzureCredential``, whose chain reads ``AZURE_CLIENT_SECRET`` from
    the environment, which is the first step toward storing one.

    What this path loses against an export, because the API allows two
    groupings: ``ChargeType`` (every record is ``usage``), ``Meter`` (no
    ``sku_id`` or description) and ``ResourceLocation`` (no ``region_id``).
    """

    provider: Provider = "azure"
    source = "azure-cost-management"
    required_roles = ("Cost Management Reader",)

    def _cache_scope(self) -> str:
        return self.settings.azure_subscription_id

    async def _fetch(self, window: DateRange) -> dict[str, Any]:
        identity = load_extra("azure")
        subscription = self.settings.azure_subscription_id
        if not subscription:
            raise LiveModeNotConfiguredError(
                "azure live mode needs CLOUDCAUSE_AZURE_SUBSCRIPTION_ID (the subscription to read; "
                "Cost Management Reader on it)"
            )
        try:
            UUID(subscription)
        except ValueError:
            raise LiveModeNotConfiguredError(
                "CLOUDCAUSE_AZURE_SUBSCRIPTION_ID must be a UUID"
            ) from None
        credential = identity.AzureCliCredential()
        try:
            token = await asyncio.to_thread(credential.get_token, AZURE_MANAGEMENT_SCOPE)
        except Exception as error:  # noqa: BLE001 - the SDK's message names the tenant
            raise LiveModeNotConfiguredError(
                f"azure live mode found no CLI identity ({type(error).__name__}): run `az login`"
            ) from None
        body = azure_query_body(window)
        headers = {"Authorization": f"Bearer {token.token}", "Content-Type": "application/json"}
        pages: list[dict[str, Any]] = []
        next_url: str | None = AZURE_QUERY_URL.format(subscription=subscription)
        seen_urls: set[str] = set()
        async with self._http_client() as client:
            while next_url:
                if len(pages) >= AZURE_MAX_PAGES:
                    raise LiveConnectorError(
                        f"azure live cost query exceeded {AZURE_MAX_PAGES} pages"
                    )
                next_url = _validated_azure_page_url(next_url, subscription)
                if next_url in seen_urls:
                    raise LiveConnectorError("azure live cost query returned a pagination loop")
                seen_urls.add(next_url)
                document = await self._post(client, next_url, body, headers)
                pages.append(document)
                properties = document.get("properties", {})
                next_url = properties.get("nextLink") or document.get("nextLink") or None
        return merge_azure_pages(pages)

    def _http_client(self) -> Any:
        """The HTTP client the fetch pages with; tests hand back one with a mock transport."""

        return httpx.AsyncClient(timeout=60.0, follow_redirects=False)

    async def _post(self, client: Any, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        """One page, with the Query API's aggressive 429s honoured via Retry-After."""

        for attempt in range(1, AZURE_MAX_ATTEMPTS + 1):
            try:
                response = await client.post(url, json=body, headers=headers)
            except httpx.TransportError:
                if attempt >= AZURE_MAX_ATTEMPTS:
                    raise
                await asyncio.sleep(min(float(2**attempt), AZURE_MAX_RETRY_WAIT_SECONDS))
                continue
            if response.status_code in AZURE_RETRYABLE_STATUSES and attempt < AZURE_MAX_ATTEMPTS:
                wait = _retry_after(response.headers.get("Retry-After"), attempt)
                if wait <= AZURE_MAX_RETRY_WAIT_SECONDS:
                    await asyncio.sleep(wait)
                    continue
            response.raise_for_status()
            if response.status_code == 204:
                return {"properties": {"columns": [], "rows": []}}
            document = response.json()
            if not isinstance(document, dict):
                raise LiveConnectorError("azure live cost query failed: response was not a JSON object")
            return document
        raise AssertionError("unreachable")

    def _map(self, raw: dict[str, Any], window: DateRange) -> tuple[list[CostRecord], datetime]:
        records = map_azure_query_response(raw, self.settings.azure_subscription_id)
        return records, azure_data_through(records, window)


def _retry_after(header: str | None, attempt: int) -> float:
    """The header's delay (seconds or HTTP-date), at least a second, else backoff."""

    delay = parse_retry_after(header)
    return max(1.0, delay) if delay is not None else float(2**attempt)


# ---------------------------------------------------------------------------
# AWS - Cost Explorer, with its own mapper
# ---------------------------------------------------------------------------


def aws_time_period(window: DateRange) -> dict[str, str]:
    """Cost Explorer's ``End`` is exclusive; ``DateRange.end`` is inclusive."""

    return {"Start": window.start.isoformat(), "End": (window.end + timedelta(days=1)).isoformat()}


def map_aws_cost_explorer_response(raw: Mapping[str, Any]) -> list[CostRecord]:
    """The AWS mapper.

    ``raw`` is ``{"account_id": ..., "batches": [{"record_type": ..., "ResultsByTime":
    [...]}]}``: one ``GetCostAndUsage`` result set per ``RECORD_TYPE`` filter, pages
    already concatenated. The record type sets ``charge_category``, since two
    group-by keys leave no room to group by it.

    What this path loses against CUR: ``resource_id`` is always ``None``
    (``RESOURCE_ID`` is not a group-by key on this API), so ``resource_key()``
    falls back to the service-level aggregate, and there are no tags.
    """

    account = str(raw.get("account_id") or "unknown")
    records: list[CostRecord] = []
    for batch in raw.get("batches", []):
        record_type = str(batch.get("record_type") or "Usage")
        charge_category = aws_charge_category(record_type)
        for result in batch.get("ResultsByTime", []):
            usage_date = date.fromisoformat(str(result["TimePeriod"]["Start"])[:10])
            for group in result.get("Groups", []):
                keys = list(group.get("Keys", []))
                service = str(keys[0]) if keys else "unknown"
                usage_type = str(keys[1]) if len(keys) > 1 else ""
                metrics = group.get("Metrics", {})
                unblended = metrics.get("UnblendedCost", {})
                amortized = metrics.get("AmortizedCost", {})
                quantity = metrics.get("UsageQuantity", {})
                billed = as_float(unblended.get("Amount"))
                records.append(
                    CostRecord(
                        provider="aws",
                        billing_account_id=account,
                        usage_date=usage_date,
                        service_name=service,
                        service_category=service_category(service),
                        charge_category=charge_category,
                        charge_description=usage_type,
                        sku_id=usage_type or None,
                        usage_quantity=as_float(quantity.get("Amount")),
                        usage_unit=str(quantity.get("Unit") or "unit"),
                        billed_cost=billed,
                        effective_cost=as_float(amortized.get("Amount"), billed),
                        currency=str(unblended.get("Unit") or "USD"),
                        source_record_id=f"aws-ce-{record_type}-{len(records)}",
                    )
                )
    return records


def aws_data_through(raw: Mapping[str, Any], window: DateRange) -> datetime:
    """The boundary is the last day Cost Explorer returned any group for.

    ``ResultsByTime[].Estimated`` is *not* the signal. Recording a real account
    showed it true for every day of the unbilled month, including days three
    back: it says "not yet invoiced", not "not yet reported". Using it would
    declare the whole current month incomplete. What Cost Explorer does not
    have yet it returns as a day with no groups, and the window never includes
    today, so the last populated day is the honest boundary - the same rule
    Azure's daily grain gets.
    """

    populated: list[date] = []
    for batch in raw.get("batches", []):
        for result in batch.get("ResultsByTime", []):
            if result.get("Groups"):
                populated.append(date.fromisoformat(str(result["TimePeriod"]["Start"])[:10]))
    if not populated:
        return end_of_day(window.start - timedelta(days=1))
    return end_of_day(max(populated))


class LiveAwsDataProvider(_LocalLiveProvider):
    """AWS costs via Cost Explorer ``GetCostAndUsage``.

    Credential: boto3's default chain - profile, SSO, environment, instance role.
    ``AWS_PROFILE`` and ``AWS_REGION`` are respected and nothing is added.
    ``sts:GetCallerIdentity`` supplies the account id the response does not carry.
    Each call bills about $0.01; one query per record type present in the window.
    """

    provider: Provider = "aws"
    source = "aws-cost-explorer"
    required_roles = ("ce:GetCostAndUsage", "ce:GetDimensionValues", "sts:GetCallerIdentity")

    def _cache_scope(self) -> str:
        # The profile is the only thing that can change which account the
        # default chain resolves to without the process restarting.
        return os.environ.get("AWS_PROFILE") or os.environ.get("AWS_DEFAULT_PROFILE", "")

    async def _fetch(self, window: DateRange) -> dict[str, Any]:
        boto3 = load_extra("aws")
        return await asyncio.to_thread(self._fetch_sync, boto3, window)

    def _fetch_sync(self, boto3: Any, window: DateRange) -> dict[str, Any]:
        session = boto3.session.Session()
        if session.get_credentials() is None:
            raise LiveModeNotConfiguredError(
                "aws live mode found no credentials in the default chain: set AWS_PROFILE or run `aws sso login`"
            )
        account = str(session.client("sts").get_caller_identity()["Account"])
        explorer = session.client("ce", region_name=AWS_COST_EXPLORER_REGION)
        period = aws_time_period(window)
        types: list[dict[str, Any]] = []
        type_token: str | None = None
        while True:
            type_request: dict[str, Any] = {
                "TimePeriod": period,
                "Dimension": "RECORD_TYPE",
                "Context": "COST_AND_USAGE",
            }
            if type_token:
                type_request["NextPageToken"] = type_token
            type_page = explorer.get_dimension_values(**type_request)
            types.extend(type_page.get("DimensionValues", []))
            type_token = type_page.get("NextPageToken") or None
            if not type_token:
                break
        batches: list[dict[str, Any]] = []
        for entry in types:
            record_type = str(entry.get("Value") or "")
            if not record_type:
                continue
            results: list[dict[str, Any]] = []
            token: str | None = None
            while True:
                request: dict[str, Any] = {
                    "TimePeriod": period,
                    "Granularity": "DAILY",
                    "Metrics": list(AWS_METRICS),
                    "GroupBy": [{"Type": "DIMENSION", "Key": key} for key in AWS_GROUP_BY],
                    "Filter": {"Dimensions": {"Key": "RECORD_TYPE", "Values": [record_type]}},
                }
                if token:
                    request["NextPageToken"] = token
                page = explorer.get_cost_and_usage(**request)
                results.extend(page.get("ResultsByTime", []))
                token = page.get("NextPageToken") or None
                if not token:
                    break
            batches.append({"record_type": record_type, "ResultsByTime": results})
        return {"account_id": account, "batches": batches}

    def _map(self, raw: dict[str, Any], window: DateRange) -> tuple[list[CostRecord], datetime]:
        return map_aws_cost_explorer_response(raw), aws_data_through(raw, window)


# ---------------------------------------------------------------------------
# GCP - BigQuery billing export, parsed by the upload parser
# ---------------------------------------------------------------------------


def validate_gcp_billing_table(table: str) -> str:
    """Prove the one SQL-interpolated string is a billing export table reference."""

    if not table:
        raise LiveModeNotConfiguredError(
            "gcp live mode needs CLOUDCAUSE_GCP_BILLING_TABLE "
            "(project.dataset.gcp_billing_export_resource_v1_XXXXXX_XXXXXX_XXXXXX)"
        )
    if not GCP_TABLE_PATTERN.match(table):
        raise LiveModeNotConfiguredError(
            "CLOUDCAUSE_GCP_BILLING_TABLE is not a billing export table reference; expected "
            "project.dataset.gcp_billing_export[_resource]_v1_<billing account id>"
        )
    return table


def gcp_billing_query(table: str) -> str:
    """One aggregated query whose rows the upload parser reads unchanged.

    Aggregating by day keeps the result small; keeping ``MAX(usage_end_time)``
    is what lets the midnight check decide whether the last day is complete.
    ``credits`` and ``labels`` are repeated records, flattened into the scalar
    and JSON shapes the parser already understands.

    Only the detailed export has a ``resource`` column. On the standard export
    the two resource columns are typed NULLs, so the query still runs and every
    record has ``resource_id=None``, the same coarseness as Cost Explorer.
    """

    table = validate_gcp_billing_table(table)
    if is_detailed_gcp_export(table):
        resource_name, resource_global_name = "resource.name", "resource.global_name"
    else:
        resource_name = resource_global_name = "CAST(NULL AS STRING)"
    return f"""
SELECT
  billing_account_id,
  DATE(usage_start_time)                        AS usage_start_time,
  MAX(usage_end_time)                           AS usage_end_time,
  service.description                           AS service_description,
  sku.id                                        AS sku_id,
  sku.description                               AS sku_description,
  location.location                             AS location_location,
  {resource_name:<45} AS resource_name,
  {resource_global_name:<45} AS resource_global_name,
  currency,
  usage.unit                                    AS usage_unit,
  SUM(usage.amount)                             AS usage_amount,
  SUM(cost)                                     AS cost,
  SUM((SELECT IFNULL(SUM(c.amount), 0) FROM UNNEST(credits) c)) AS credits_amount,
  TO_JSON_STRING(ARRAY(
    SELECT AS STRUCT label.key, label.value
    FROM UNNEST(labels) AS label
    ORDER BY label.key, label.value
  ))                                             AS labels
FROM `{table}`
WHERE _PARTITIONTIME >= @partition_floor
  AND usage_start_time >= @window_start
  AND usage_start_time <  @window_end_exclusive
GROUP BY 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 15
""".strip()


def is_detailed_gcp_export(table: str) -> bool:
    """Whether the table is the detailed (resource-level) billing export."""

    return ".gcp_billing_export_resource_v1_" in table


def gcp_query_bounds(window: DateRange) -> dict[str, datetime]:
    start = datetime.combine(window.start, time(0, 0), tzinfo=UTC)
    return {
        "partition_floor": start - timedelta(days=GCP_PARTITION_LAG_DAYS),
        "window_start": start,
        "window_end_exclusive": datetime.combine(window.end + timedelta(days=1), time(0, 0), tzinfo=UTC),
    }


def gcp_export_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rename the query's underscore aliases back to the export's dotted columns."""

    return [{GCP_COLUMN_ALIASES.get(str(key), str(key)): value for key, value in row.items()} for row in rows]


def map_gcp_export_rows(rows: Sequence[Mapping[str, Any]]) -> list[CostRecord]:
    """The GCP mapper: rename, then the upload parser does the rest."""

    return parse_gcp_billing_export(gcp_export_rows(rows))


def gcp_live_data_through(
    rows: Sequence[Mapping[str, Any]], records: Sequence[CostRecord], window: DateRange
) -> datetime:
    """The upload rule, applied to query rows: the last day must reach midnight."""

    if not records:
        return end_of_day(window.start - timedelta(days=1))
    boundary, _note = gcp_data_through(gcp_export_rows(rows), records)
    return boundary


class LiveGcpDataProvider(_LocalLiveProvider):
    """GCP costs via the Cloud Billing BigQuery export.

    There is no cost API on GCP; the export is the sanctioned source, and it
    only holds data from when it was enabled (a US/EU multi-region dataset
    backfills to the start of the previous month). Enable the *detailed* export:
    the parser reads ``resource.global_name`` for ``resource_id``.

    Credential: Application Default Credentials. ``gcloud auth login`` does
    not set them; ``gcloud auth application-default login`` does. Roles:
    BigQuery Data Viewer on the dataset, BigQuery Job User on the project.
    """

    provider: Provider = "gcp"
    source = "gcp-billing-export-bigquery"
    required_roles = ("BigQuery Data Viewer", "BigQuery Job User")

    def _cache_scope(self) -> str:
        return self.settings.gcp_billing_table

    async def _fetch(self, window: DateRange) -> list[dict[str, Any]]:
        bigquery = load_extra("gcp")
        table = validate_gcp_billing_table(self.settings.gcp_billing_table)
        return await asyncio.to_thread(self._fetch_sync, bigquery, table, window)

    def _fetch_sync(self, bigquery: Any, table: str, window: DateRange) -> list[dict[str, Any]]:
        project = table.split(".", 1)[0]
        try:
            client = bigquery.Client(project=project)
        except Exception as error:  # noqa: BLE001 - google.auth's message names the machine's ADC path
            if type(error).__name__ == "DefaultCredentialsError":
                raise LiveModeNotConfiguredError(
                    "gcp live mode found no Application Default Credentials: run "
                    "`gcloud auth application-default login`"
                ) from None
            raise
        bounds = gcp_query_bounds(window)
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(name, "TIMESTAMP", value)
                for name, value in bounds.items()
            ],
            maximum_bytes_billed=self.settings.gcp_max_bytes_billed,
        )
        rows = client.query(gcp_billing_query(table), job_config=job_config).result()
        return [dict(row.items()) for row in rows]

    def _map(self, raw: list[dict[str, Any]], window: DateRange) -> tuple[list[CostRecord], datetime]:
        records = map_gcp_export_rows(raw)
        return records, gcp_live_data_through(raw, records, window)
