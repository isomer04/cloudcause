# Fixtures

Synthetic data for the default demo scenario. No real account, project, or
subscription is represented: identifiers use documentation ranges
(`203.0.113.0/24`, `198.51.100.0/24`, `example` domains) and made-up ids.

Regenerate after editing `generate_fixtures.py`:

```bash
python fixtures/generate_fixtures.py
```

CI regenerates and fails on a diff, so the committed files always match the
generator.

`aws/`, `azure/`, and `gcp/` are the demo scenario the generator owns.
[`uploads/`](uploads/README.md) is separate: hand-written provider-native exports
for trying the **Your data** flow, not read by any provider adapter and not
touched by `generate_fixtures.py`.

## Periods and planted causes

* Baseline: 2026-07-06 to 2026-07-12
* Current: 2026-07-13 to 2026-07-19
* Data through: 2026-07-19T23:59:59Z, retrieved 2026-07-20T09:00:00Z

| Provider | Planted cause | Cost impact |
| --- | --- | --- |
| AWS | Route change sends S3 traffic through NAT Gateway `nat-0ab12cd34ef56789a` after the S3 gateway endpoint is deleted | +126.00 USD |
| AWS | Forgotten sandbox instance `i-0dev1234567890abc` | +28.80 USD |
| Azure | Retry loop in Function App `orders-processor` after a deployment | +103.20 USD |
| GCP | Exposed API key drives Cloud Translation usage from two unrecognized networks | +161.60 USD |

The four planted causes account for +419.60 USD of a +430.26 USD measured change.

## Planted drift: the part nothing explains

Each provider also carries one untagged, resource-less SKU whose usage creeps up
across the current week at an unchanged unit rate:

| Provider | Drifting SKU | Cost impact |
| --- | --- | --- |
| AWS | `CW:MetricMonitorUsage` custom metrics | +2.89 USD |
| Azure | `Data Transfer Out - Zone 1` | +3.85 USD |
| GCP | `Network Internet Egress from Americas to Americas` | +3.92 USD |

Each sits under the 5.00 USD materiality floor, so none becomes a candidate and
none can be attributed. That is the point. Real exports leave something over —
diffuse movement across untagged SKUs, rounding, credits — and a demo that
reconciles to exactly 0.00 on every provider simultaneously reads as a fixture
built backwards from its answer. It also leaves the reconciler's tolerance band
invisible, because nothing ever lands inside it. The residual makes the band
observable: +10.66 USD unattributed of +430.26 USD, within tolerance, declared.

Keep any new drift under `AnalyticsConfig.min_absolute_change` and the total
residual under `reconciliation_tolerance` (5% of the total change), or the demo
will publish an outside-tolerance warning instead.

## File formats

Cost data keeps the provider-native export shape and goes through the same
parsers a live connector will use, so switching `CLOUDCAUSE_DATA_MODE` cannot
change the normalized result:

| File | Format |
| --- | --- |
| `aws/cost_and_usage.json` | AWS Data Exports / CUR 2.0 column names, under `rows` |
| `azure/cost_management.json` | Azure Cost Management query result: `properties.columns` + `properties.rows`, `UsageDate` as `yyyymmdd` |
| `gcp/billing_export.csv` | BigQuery detailed usage export columns, including `credits.amount` and `labels` |

Inventory, metrics, audit events, and recommendations use the CloudCause fixture
shape: a JSON object with an `items` array whose entries validate directly into
the shared contracts (`CloudResource`, `MetricSeries`, `AuditEvent`,
`Recommendation`). Live adapters will map real API responses into those same
models, which is exactly what the contract tests assert.

`manifest.json` carries the provenance every tool response must return: source
name, schema version, `observed_at`, `retrieved_at`, and `data_through`. The
freshness rules in `knowledge/` decide how a gap between `data_through` and the
requested period end must be reported. It is never treated as zero usage.

## Uploading your own data in these shapes

The same four shapes are what `PUT /api/v1/datasets/{id}/sources/{provider}/{kind}`
accepts for Tier 2 evidence, and they are the difference between a report that
measures a cost change and one that names its cause. Provider-native shapes
(CloudWatch `GetMetricData`, CloudTrail `LookupEvents`, Azure Monitor, Cloud
Monitoring) are a follow-up, not supported yet.

| Upload kind | Shape | Model |
| --- | --- | --- |
| `metrics` | `{"items": [...]}` | `MetricSeries` |
| `audit` | `{"items": [...]}` | `AuditEvent` |
| `inventory` | `{"items": [...]}` | `CloudResource` |
| `recommendations` | `{"items": [...]}` | `Recommendation` |

The `provider` field is filled in from the URL, so leave it out. A ready-to-edit
template for each is served from the gateway, cut from the files in this
directory so it can never drift from the model that validates it:

```bash
curl -s localhost:8000/api/v1/datasets/templates/metrics
```

The Next.js UI links the same four downloads next to its drop zones. Cost exports
need no template: upload the provider's own export unchanged.

## More scenarios

Thirteen additional single-provider scenarios live in `evaluations/scenarios/` as
compact YAML and are expanded in memory by the same adapter boundary. See
`evaluations/README.md`.
