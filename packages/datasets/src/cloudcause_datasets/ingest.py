"""Turn one uploaded file into normalized records, or refuse it clearly.

Everything here works on bytes already held in memory, never on a path, because
the gateway reads the raw request stream and discards it. Starlette's
``UploadFile`` is a ``SpooledTemporaryFile`` that flushes to a real temp file
above 1 MB, so the obvious FastAPI signature would put every file that matters on
disk; the ingest endpoints deliberately do not use it.

Refusals name columns and row numbers. They never quote a row value, so an error
message cannot hand somebody's billing data to a log.
"""

from __future__ import annotations

import csv
import io
import json
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any

from cloudcause_contracts import (
    ACCEPTED_CONTENT_TYPES,
    AuditEvent,
    CloudResource,
    CostRecord,
    DatasetRowRejection,
    DatasetSourceKind,
    MetricSeries,
    Provider,
    Recommendation,
    Settings,
)
from cloudcause_focus import (
    parse_aws_rows,
    parse_azure_cost_management,
    parse_gcp_billing_export,
)

GZIP_MAGIC = b"\x1f\x8b"

#: Refused as a decompression bomb past this expansion, checked as it streams.
MAX_EXPANSION_RATIO = 200

#: A cost export is not a secret, so anything shaped like one is a mistake or an
#: attack. Either way it is refused rather than stored.
CREDENTIAL_FIELD_MARKERS = (
    "aws_secret_access_key",
    "aws_session_token",
    "secret_access_key",
    "client_secret",
    "private_key",
    "private_key_id",
    "refresh_token",
    "password",
    "sas_token",
    "connection_string",
)

#: Columns that identify each provider's export by content, not by the URL.
AWS_COST_MARKERS = ("line_item_usage_start_date", "identity_line_item_id")
GCP_COST_MARKERS = ("usage_start_time", "service.description")
AZURE_COST_MARKER = "UsageDate"

EVIDENCE_MODELS: dict[DatasetSourceKind, Any] = {
    "metrics": MetricSeries,
    "audit": AuditEvent,
    "inventory": CloudResource,
    "recommendations": Recommendation,
}

#: The daily aggregation key. Dropping ``billing_account_id`` would break the
#: ``account`` grouping dimension that ``group_changes`` and the MCP
#: ``get_cost_breakdown`` tool advertise; dropping ``currency`` would hide the
#: mixing that :func:`check_single_currency` refuses outright.
AGGREGATION_FIELDS = (
    "provider",
    "billing_account_id",
    "usage_date",
    "service_name",
    "sku_id",
    "region_id",
    "resource_id",
    "charge_category",
    "currency",
)


class IngestError(ValueError):
    """The upload cannot be used. ``status`` decides 413 versus 422."""

    status = 422
    code = "unprocessable_upload"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code


class UploadTooLargeError(IngestError):
    """Over a byte or row cap. Rejected at the first byte past the limit."""

    status = 413
    code = "upload_too_large"


class UnsupportedContentTypeError(IngestError):
    code = "unsupported_content_type"


class FormatMismatchError(IngestError):
    """The content is not the provider's export, whatever the URL claimed."""

    code = "provider_format_mismatch"


class CurrencyConflictError(IngestError):
    code = "mixed_currency"


class CredentialInUploadError(IngestError):
    code = "credential_shaped_field"


@dataclass
class ParsedSource:
    """One accepted file: what was stored, and everything that was not."""

    kind: DatasetSourceKind
    provider: Provider
    detected_format: str
    raw_rows: int = 0
    accepted_rows: int = 0
    costs: list[CostRecord] = field(default_factory=list)
    metrics: list[MetricSeries] = field(default_factory=list)
    audit_events: list[AuditEvent] = field(default_factory=list)
    resources: list[CloudResource] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    rejections: list[DatasetRowRejection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data_through: datetime | None = None
    data_through_note: str = ""
    period_start: date | None = None
    period_end: date | None = None
    currency: str | None = None
    compressed: bool = False

    def stored_records(self) -> int:
        return (
            len(self.costs)
            + len(self.metrics)
            + len(self.audit_events)
            + len(self.resources)
            + len(self.recommendations)
        )


# --------------------------------------------------------------------- transport


def check_content_type(content_type: str | None) -> str:
    """Allowlist the media type before a byte is parsed."""

    value = (content_type or "").split(";")[0].strip().lower()
    if value in ACCEPTED_CONTENT_TYPES:
        return value
    if value in ("application/x-gzip", "application/x-gzip-compressed"):
        return "application/gzip"
    raise UnsupportedContentTypeError(
        f"content-type {value or 'missing'!r} is not accepted; use one of "
        f"{', '.join(ACCEPTED_CONTENT_TYPES)}"
    )


def decompress_if_needed(payload: bytes, settings: Settings) -> tuple[bytes, bool]:
    """Expand a single-member gzip within a size and ratio cap, or pass through.

    Three guards, because a 25 MB upload can hide a 20 GB expansion: the magic
    bytes must match, the decompressed size is capped as it streams out, and the
    expansion ratio is capped so a bomb is refused before the size cap is hit. A
    second gzip member is refused too, since it would hide content behind the
    first one that was checked.
    """

    if not payload.startswith(GZIP_MAGIC):
        return payload, False
    limit = settings.upload_max_decompressed_bytes
    engine = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = bytearray()
    pending = payload
    try:
        while True:
            out.extend(engine.decompress(pending, 1024 * 1024))
            if len(out) > limit:
                raise UploadTooLargeError(
                    f"the gzip member expands past the {limit:,} byte decompressed limit"
                )
            # Checked per chunk, not at the end: a bomb is refused after a few
            # hundred kilobytes instead of after expanding to the size cap.
            if len(out) / max(len(payload), 1) > MAX_EXPANSION_RATIO:
                raise UploadTooLargeError(
                    f"the gzip expansion ratio passed {MAX_EXPANSION_RATIO}x, which is refused as "
                    "a decompression bomb"
                )
            pending = engine.unconsumed_tail
            if not pending:
                break
    except zlib.error as error:
        raise IngestError(f"the gzip member could not be read: {error}") from error
    if not engine.eof:
        raise IngestError("the gzip member is truncated")
    if engine.unused_data:
        raise IngestError(
            "gzip uploads must contain exactly one member; a second member would hide content "
            "behind the one that was checked"
        )
    return bytes(out), True


def refuse_credential_shaped_content(payload: bytes) -> None:
    """Refuse anything carrying a credential-shaped field name.

    Checked on the raw text rather than the parsed rows so it also covers a JSON
    body that never validates into a contract object, and over the whole payload
    rather than a prefix so a secret cannot hide past the first megabyte.
    """

    lowered = payload.lower()
    for marker in CREDENTIAL_FIELD_MARKERS:
        if marker.encode() in lowered:
            raise CredentialInUploadError(
                f"the upload contains a field named like a credential ({marker}). A cost export "
                "needs no secret, so it is refused rather than stored."
            )


# ------------------------------------------------------------------- cost parsing


def _decode(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise IngestError("the file is not UTF-8 or UTF-16 text")


def _csv_rows(text: str, settings: Settings) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    for row in reader:
        rows.append(row)
        if len(rows) > settings.upload_max_rows:
            raise UploadTooLargeError(
                f"the file holds more than {settings.upload_max_rows:,} rows"
            )
    return rows


def _json_document(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise IngestError(
            f"the file is not valid JSON: {error.msg} at line {error.lineno}, column {error.colno}"
        ) from error


def sniff_cost_format(provider: Provider, payload: bytes) -> str:
    """Identify the export by content and confirm it matches ``provider``.

    Detection is deliberately not driven by the ``provider`` in the URL: a user
    who drops an Azure export in the AWS slot gets a 422 naming the columns that
    were expected and the ones that were found, not a silently empty comparison.
    """

    return _sniff_cost(provider, payload)[0]


def _sniff_cost(provider: Provider, payload: bytes) -> tuple[str, str, Any | None]:
    """``sniff_cost_format``, also returning the decoded text and parsed document.

    The caller needs both to parse rows, and decoding a 25 MB export or parsing
    its JSON twice is pure waste. ``document`` is ``None`` for CSV.
    """

    text = _decode(payload)
    stripped = text.lstrip()
    document: Any | None = None
    if stripped.startswith(("{", "[")):
        document = _json_document(text)
        detected, found = _sniff_json_cost(document)
    else:
        header = _csv_header(text)
        detected, found = _sniff_csv_cost(header)
    if detected is None:
        raise FormatMismatchError(
            "the file does not look like a supported cost export. Expected AWS CUR columns "
            f"({', '.join(AWS_COST_MARKERS)}), a GCP billing export ({', '.join(GCP_COST_MARKERS)}), "
            f"or an Azure Cost Management query result (columns including {AZURE_COST_MARKER}). "
            f"Found: {found or 'no recognizable columns'}"
        )
    if not detected.startswith(provider):
        raise FormatMismatchError(
            f"this file is a {detected} export but it was uploaded as {provider}. Upload it under "
            f"/sources/{detected.split('-')[0]}/cost instead."
        )
    return detected, text, document


def _csv_header(text: str) -> list[str]:
    reader = csv.reader(io.StringIO(text))
    try:
        return [column.strip() for column in next(reader)]
    except StopIteration:
        raise IngestError("the CSV file is empty") from None


def _sniff_csv_cost(header: Sequence[str]) -> tuple[str | None, str]:
    columns = set(header)
    found = ", ".join(sorted(columns)[:8])
    if any(marker in columns for marker in AWS_COST_MARKERS):
        return "aws-cur-csv", found
    if any(marker in columns for marker in GCP_COST_MARKERS):
        return "gcp-billing-export-csv", found
    return None, found


def _sniff_json_cost(document: Any) -> tuple[str | None, str]:
    if isinstance(document, Mapping):
        properties = document.get("properties", document)
        if isinstance(properties, Mapping) and "columns" in properties and "rows" in properties:
            names = [
                str(column.get("name"))
                for column in properties.get("columns", [])
                if isinstance(column, Mapping)
            ]
            if AZURE_COST_MARKER in names:
                return "azure-cost-management-json", ", ".join(names[:8])
            return None, ", ".join(names[:8]) or "columns without a UsageDate"
        rows = document.get("rows")
        if isinstance(rows, list) and rows and isinstance(rows[0], Mapping):
            detected, found = _sniff_csv_cost(list(rows[0].keys()))
            return detected, found
        items = document.get("items")
        if isinstance(items, list) and items and isinstance(items[0], Mapping):
            detected, found = _sniff_csv_cost(list(items[0].keys()))
            return detected, found
    if isinstance(document, list) and document and isinstance(document[0], Mapping):
        return _sniff_csv_cost(list(document[0].keys()))
    return None, "no recognizable columns"


def _cost_rows(
    detected: str, text: str, document: Any | None, settings: Settings
) -> list[Mapping[str, Any]]:
    if detected.endswith("csv") and document is None:
        return _csv_rows(text, settings)
    if document is None:  # pragma: no cover - sniffing decided the shape already
        document = _json_document(text)
    if isinstance(document, list):
        rows = document
    elif isinstance(document, Mapping):
        rows = document.get("rows") or document.get("items") or []
    else:  # pragma: no cover - sniffing already refused these
        rows = []
    if len(rows) > settings.upload_max_rows:
        raise UploadTooLargeError(f"the file holds more than {settings.upload_max_rows:,} rows")
    return [row for row in rows if isinstance(row, Mapping)]


def parse_cost_source(provider: Provider, payload: bytes, settings: Settings) -> ParsedSource:
    """Parse, validate, date, and aggregate one cost export."""

    detected, text, document = _sniff_cost(provider, payload)
    result = ParsedSource(kind="cost", provider=provider, detected_format=detected)

    if detected == "azure-cost-management-json":
        document = document if document is not None else _json_document(text)
        properties = document.get("properties", document)
        raw_rows = list(properties.get("rows", []))
        if len(raw_rows) > settings.upload_max_rows:
            raise UploadTooLargeError(f"the file holds more than {settings.upload_max_rows:,} rows")
        result.raw_rows = len(raw_rows)
        records = parse_azure_cost_management(document)
        result.data_through, result.data_through_note = _azure_data_through(records)
    elif detected.startswith("aws"):
        rows = _cost_rows(detected, text, document, settings)
        result.raw_rows = len(rows)
        records = parse_aws_rows(rows)
        result.data_through, result.data_through_note = _aws_data_through(rows, records)
    else:
        rows = _cost_rows(detected, text, document, settings)
        result.raw_rows = len(rows)
        records = parse_gcp_billing_export(rows)
        result.data_through, result.data_through_note = _gcp_data_through(rows, records)

    usable, rejections = _validate_cost_records(records)
    result.rejections.extend(rejections)
    result.accepted_rows = len(usable)
    if not usable:
        raise IngestError(
            "no usable cost rows were found. "
            + (
                f"{len(rejections)} row(s) were rejected; the first reason was "
                f"{rejections[0].code}: {rejections[0].detail}"
                if rejections
                else "the file parsed but contained no rows."
            )
        )

    result.currency = check_single_currency(usable)
    result.costs = aggregate_cost_records(usable)
    dates = [record.usage_date for record in result.costs]
    result.period_start, result.period_end = min(dates), max(dates)
    if result.raw_rows and len(result.costs) < result.raw_rows:
        result.warnings.append(
            f"{result.raw_rows:,} raw rows were collapsed to {len(result.costs):,} daily rows at the "
            "grain the analytics layer consumes. The raw count is reported here and then forgotten."
        )
    if result.data_through_note:
        result.warnings.append(result.data_through_note)
    return result


def _validate_cost_records(
    records: Sequence[CostRecord],
) -> tuple[list[CostRecord], list[DatasetRowRejection]]:
    """Drop rows that cannot carry a comparison, naming the column at fault."""

    usable: list[CostRecord] = []
    rejections: list[DatasetRowRejection] = []
    for index, record in enumerate(records, start=1):
        if not record.service_name or record.service_name == "unknown":
            rejections.append(
                DatasetRowRejection(
                    row_number=index,
                    code="missing_service",
                    detail="no service name column could be read for this row",
                )
            )
            continue
        if record.billed_cost == 0.0 and record.effective_cost == 0.0 and record.usage_quantity == 0.0:
            rejections.append(
                DatasetRowRejection(
                    row_number=index,
                    code="empty_row",
                    detail="cost and usage columns were both zero or absent",
                )
            )
            continue
        usable.append(record)
    return usable, rejections


def check_single_currency(records: Iterable[CostRecord]) -> str:
    """One currency per dataset, or a refusal naming both.

    ``compare_periods`` assigns ``self.currency = record.currency or self.currency``
    with the last row winning, so a mixed-currency upload would sum unlike money
    under one arbitrary label. The fixtures are USD only, which is why no existing
    test catches it and why this has to be refused at the door.
    """

    currencies = sorted({record.currency for record in records if record.currency})
    if len(currencies) > 1:
        raise CurrencyConflictError(
            f"the file mixes {len(currencies)} currencies ({', '.join(currencies)}). CloudCause "
            "does not convert between them, so one dataset carries one currency. Split the export "
            "by currency and upload them as separate datasets."
        )
    return currencies[0] if currencies else "USD"


def aggregate_cost_records(records: Sequence[CostRecord]) -> list[CostRecord]:
    """Collapse rows to daily grain, which is what the analytics layer consumes.

    An hourly CUR typically shrinks by one to two orders of magnitude here, and
    the grain is unchanged from what the fixtures already feed the comparison.
    """

    buckets: dict[tuple, CostRecord] = {}
    for record in records:
        key = (
            *(getattr(record, name) for name in AGGREGATION_FIELDS),
            tuple(sorted(record.tags.items())),
        )
        existing = buckets.get(key)
        if existing is None:
            buckets[key] = record.model_copy(deep=True)
            continue
        existing.usage_quantity = round(existing.usage_quantity + record.usage_quantity, 6)
        existing.billed_cost = round(existing.billed_cost + record.billed_cost, 6)
        existing.effective_cost = round(existing.effective_cost + record.effective_cost, 6)
        existing.commitment_discount_id = existing.commitment_discount_id or record.commitment_discount_id
        existing.resource_name = existing.resource_name or record.resource_name
    aggregated = list(buckets.values())
    aggregated.sort(
        key=lambda record: (record.usage_date, record.service_name, record.resource_key())
    )
    # Numbered after the sort so the ids follow the order a reader sees.
    for index, record in enumerate(aggregated):
        record.source_record_id = f"upload-{record.usage_date.isoformat()}-{index:06d}"
    return aggregated


# --------------------------------------------------------------- data_through


def _end_of_day(day: date) -> datetime:
    return datetime.combine(day, time(23, 59, 59), tzinfo=UTC)


def _as_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _aws_data_through(
    rows: Sequence[Mapping[str, Any]], records: Sequence[CostRecord]
) -> tuple[datetime, str]:
    """AWS publishes hourly rows, so a short final day is visibly incomplete.

    Fewer than 24 distinct start hours on the last date means the export was cut
    mid-day. That question can only be asked before aggregation: once rows are
    collapsed to a daily grain, a partial day is indistinguishable from a quiet one.
    """

    if not records:  # pragma: no cover - callers reject empty files first
        return datetime.now(tz=UTC), ""
    last_date = max(record.usage_date for record in records)
    hours: set[int] = set()
    for row in rows:
        stamp = _as_datetime(row.get("line_item_usage_start_date"))
        if stamp is not None and stamp.date() == last_date:
            hours.add(stamp.hour)
    if len(hours) >= 24:
        return _end_of_day(last_date), ""
    earlier = [record.usage_date for record in records if record.usage_date < last_date]
    if not earlier:
        return _end_of_day(last_date), (
            f"{last_date.isoformat()} carries only {len(hours)} of 24 hourly buckets and is the only "
            "date in the file, so coverage is reported through it but treated as provisional."
        )
    return _end_of_day(max(earlier)), (
        f"{last_date.isoformat()} carries only {len(hours)} of 24 hourly buckets, so it is a partial "
        f"day. Coverage is reported through {max(earlier).isoformat()}; the missing hours are "
        "unavailable data, never zero usage."
    )


def _gcp_data_through(
    rows: Sequence[Mapping[str, Any]], records: Sequence[CostRecord]
) -> tuple[datetime, str]:
    """GCP carries ``usage_end_time``, so the last day is checked against midnight."""

    if not records:  # pragma: no cover - callers reject empty files first
        return datetime.now(tz=UTC), ""
    last_date = max(record.usage_date for record in records)
    ends = [
        stamp
        for stamp in (_as_datetime(row.get("usage_end_time")) for row in rows)
        if stamp is not None and stamp.date() >= last_date
    ]
    if not ends:
        return _end_of_day(last_date), (
            f"no usage_end_time column was present, so {last_date.isoformat()} is taken as complete."
        )
    latest = max(ends)
    midnight = datetime.combine(last_date, time(0, 0), tzinfo=UTC)
    if (latest - midnight).total_seconds() >= 24 * 3600 - 1:
        return _end_of_day(last_date), ""
    earlier = [record.usage_date for record in records if record.usage_date < last_date]
    boundary = _end_of_day(max(earlier)) if earlier else latest
    return boundary, (
        f"usage on {last_date.isoformat()} ends at {latest.time().isoformat()} rather than midnight, "
        f"so it is a partial day. Coverage is reported through {boundary.isoformat()}."
    )


def _azure_data_through(records: Sequence[CostRecord]) -> tuple[datetime, str]:
    """Azure Cost Management returns daily grain with no intraday signal.

    There is nothing in the file that could distinguish a partial final day from a
    quiet one, so the maximum date is taken as complete and the summary says so.
    That is the honest answer; guessing would be worse.
    """

    if not records:  # pragma: no cover - callers reject empty files first
        return datetime.now(tz=UTC), ""
    last_date = max(record.usage_date for record in records)
    return _end_of_day(last_date), (
        f"Azure Cost Management results carry daily grain with no intraday detail, so "
        f"{last_date.isoformat()} is taken as complete. If that day was still accruing, treat "
        "conclusions about it as provisional."
    )


# --------------------------------------------------------------- evidence parsing


def parse_evidence_source(
    provider: Provider, kind: DatasetSourceKind, payload: bytes, settings: Settings
) -> ParsedSource:
    """Parse one of the four documented CloudCause evidence shapes."""

    model = EVIDENCE_MODELS.get(kind)
    if model is None:  # pragma: no cover - the router validates kind first
        raise IngestError(f"{kind} is not an evidence source kind")
    document = _json_document(_decode(payload))
    if isinstance(document, list):
        items = document
    elif isinstance(document, Mapping):
        items = document.get("items")
        if items is None:
            raise IngestError(
                "expected a JSON object with an 'items' array, as documented in "
                "fixtures/README.md. Provider-native shapes are not accepted yet."
            )
    else:
        raise IngestError("expected a JSON object with an 'items' array")
    if not isinstance(items, list):
        raise IngestError("'items' must be an array")
    if len(items) > settings.upload_max_rows:
        raise UploadTooLargeError(f"the file holds more than {settings.upload_max_rows:,} items")

    result = ParsedSource(
        kind=kind, provider=provider, detected_format=f"cloudcause-{kind}-json", raw_rows=len(items)
    )
    parsed: list[Any] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            result.rejections.append(
                DatasetRowRejection(
                    row_number=index, code="not_an_object", detail="array entries must be objects"
                )
            )
            continue
        # The provider comes from the URL and wins: an item claiming another
        # provider would otherwise be filed under it and never be read back.
        candidate = {**item, "provider": provider}
        try:
            parsed.append(model.model_validate(candidate))
        except Exception as error:  # noqa: BLE001 - reported as a row rejection
            result.rejections.append(
                DatasetRowRejection(
                    row_number=index,
                    code="schema_mismatch",
                    detail=_field_errors(error),
                )
            )
    if not parsed:
        detail = result.rejections[0].detail if result.rejections else "the array was empty"
        raise IngestError(f"no usable {kind} items were found: {detail}")

    result.accepted_rows = len(parsed)
    if kind == "metrics":
        result.metrics = parsed
        stamps = [point.timestamp for series in parsed for point in series.points]
    elif kind == "audit":
        result.audit_events = parsed
        stamps = [event.event_time for event in parsed]
    elif kind == "inventory":
        result.resources = parsed
        # created_at is when a resource was born, not a window this file covers,
        # so inventory reports no period at all rather than a misleading one.
        stamps = []
    else:
        result.recommendations = parsed
        stamps = []
    if stamps:
        result.data_through = max(stamps)
        result.period_start = min(stamps).date()
        result.period_end = max(stamps).date()
    else:
        result.data_through = datetime.now(tz=UTC)
        result.data_through_note = (
            f"{kind} carries no period of its own, so its freshness is recorded as the upload time."
        )
        result.warnings.append(result.data_through_note)
    return result


def _field_errors(error: Exception) -> str:
    """Name the offending fields without echoing their values."""

    errors = getattr(error, "errors", None)
    if not callable(errors):
        return type(error).__name__
    names: list[str] = []
    for entry in errors():
        location = ".".join(str(part) for part in entry.get("loc", ())) or "(root)"
        names.append(f"{location} ({entry.get('type', 'invalid')})")
    return "invalid or missing fields: " + ", ".join(sorted(set(names))[:6])


def parse_source(
    provider: Provider, kind: DatasetSourceKind, payload: bytes, settings: Settings
) -> ParsedSource:
    """Parse whichever kind was addressed by the URL."""

    refuse_credential_shaped_content(payload)
    expanded, compressed = decompress_if_needed(payload, settings)
    refuse_credential_shaped_content(expanded)
    result = (
        parse_cost_source(provider, expanded, settings)
        if kind == "cost"
        else parse_evidence_source(provider, kind, expanded, settings)
    )
    result.compressed = compressed
    return result
