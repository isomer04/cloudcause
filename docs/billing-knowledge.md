# Billing knowledge and provider updates

CloudCause separates *operational data* from *billing knowledge*. Operational data
describes what happened in an account. Billing knowledge explains how provider rules,
schemas, prices, discounts, and data delays should be interpreted, and it is versioned
and dated because provider billing behaviour changes over time.

This is the subsystem behind the 51 rules under [`../knowledge/`](../knowledge/) and the
read-only Billing Knowledge MCP server. See also
[`adr/0004`](adr/0004-native-tools-and-mcp.md) for why the rule store sits behind MCP
rather than a native tool.

CloudCause must separate current operational data from billing knowledge. Operational data describes what happened in an account; billing knowledge explains how provider rules, schemas, prices, discounts, and data delays should be interpreted.

Provider billing behavior changes over time. The system must track:

- Billing and export schemas
- API versions and deprecations
- Pricing dimensions and SKUs
- Credits, reservations, savings plans, and commitments
- Service-specific cost drivers
- Known data delays and invoice-versus-estimate differences
- FOCUS specification versions
- Effective dates for every interpretation rule

## 1. Runtime architecture: HTTP between services, MCP for tools

The HTTP service boundaries in Section 5 remain responsible for agent-to-agent communication. MCP is the read-only tool boundary inside each provider investigator.

```text
Google ADK Coordinator + GCP Investigator
├── HTTP → AWS Strands Investigator
│            ├── MCP → AWS operational-data tools
│            └── MCP → Billing Knowledge tools
├── HTTP → Azure MAF Investigator
│            ├── MCP → Azure operational-data tools
│            └── MCP → Billing Knowledge tools
└── MCP → GCP operational-data and Billing Knowledge tools

Deterministic Python
├── FOCUS normalization
├── Anomaly detection
├── Cost reconciliation
├── Freshness validation
└── Evidence validation
```

Use HTTP between independently deployable framework services. Use MCP for controlled agent tool access. Do not use Playwright or web scraping to retrieve cloud billing data.

## 2. Billing Knowledge MCP server

Add one read-only `cloudcause-billing-knowledge` MCP server backed by reviewed, versioned rules.

Initial tools:

```text
get_billing_rule
get_cost_driver_definitions
get_provider_data_freshness_rules
get_export_schema_version
get_api_deprecation_status
get_pricing_source
get_known_billing_change
```

Every response must include:

- Provider and service
- Rule identifier and schema version
- `valid_from` and optional `valid_to`
- Official source URL
- Source update date when available
- Internal review date
- Confidence or support status

The tool must select rules according to the usage date. It must not apply a current rule retroactively to an older billing period.

Example rule:

```yaml
id: aws-nat-gateway-data-processing
provider: aws
service: nat-gateway
valid_from: 2025-01-01
valid_to: null
reviewed_at: 2026-07-27
cost_drivers:
  - hourly_gateway_charge
  - processed_data
  - cross_availability_zone_transfer
investigation_checks:
  - compare_bytes_processed
  - inspect_route_changes
  - identify_traffic_destination
  - check_for_vpc_endpoint
source:
  type: official_documentation
  url: https://docs.aws.amazon.com/vpc/latest/userguide/nat-gateway-pricing.html
```

## 3. Knowledge repository

```text
knowledge/
├── aws/
│   ├── cost-explorer.yaml
│   ├── nat-gateway.yaml
│   ├── savings-plans.yaml
│   └── data-freshness.yaml
├── azure/
│   ├── cost-management.yaml
│   ├── functions.yaml
│   ├── reservations.yaml
│   └── api-versions.yaml
├── gcp/
│   ├── billing-export.yaml
│   ├── committed-use-discounts.yaml
│   ├── network-egress.yaml
│   └── data-freshness.yaml
└── focus/
    └── 1.4.yaml
```

CloudCause pins to FOCUS 1.4. Reject or quarantine unknown future schema versions instead of silently interpreting them with the 1.4 parser.

## 4. Official update sources

Monitor official sources rather than general search results.

AWS:

- AWS Cost Management document history
- Cost Explorer and Billing API references
- AWS What's New
- Cost and Usage Report/Data Exports schemas
- AWS pricing APIs and service-specific pricing documentation

Azure:

- Microsoft Cost Management documentation
- Azure Updates and retirement notices
- Cost Management API versions
- Azure SDK changelogs
- Retail Prices API
- Azure Advisor changes

Google Cloud:

- Cloud Billing release notes
- Billing export and BigQuery schema documentation
- Cloud Billing Catalog API
- Recommender release notes
- Commitment and discount-model changes

FOCUS:

- Official specification
- Changelog
- Provider compatibility notes

Context7 may assist developers with current ADK, MAF, Strands, SDK, FastAPI, Pydantic, and MCP documentation. It is development-only and is not an authoritative source for provider billing semantics.

## 5. Controlled update workflow

```text
Official documentation and release notes
               |
        Scheduled change check
               |
       Candidate documentation change
               |
          Human review
               |
      Versioned rule or adapter update
               |
      Fixture and regression updates
               |
        Pull-request validation
```

Do not automatically deploy logic generated from changed documentation. A documentation change may be editorial, regional, preview-only, a deprecation, a schema change, or a true billing-rule change. Human review determines whether code, rules, fixtures, or tests must change.

In practice:

- Check official update channels weekly through a scheduled CI job.
- Record source and review dates.
- Open a report or pull request when monitored content changes.
- Never let the scheduled job change production rules directly.
- Review pricing sources before publishing a demonstration.
- Check SDK and API deprecations during dependency upgrades.

## 6. Freshness and provenance

Operational tools and fixtures must return:

```json
{
  "provider": "aws",
  "source": "cost-explorer",
  "observed_at": "2026-07-27T12:00:00Z",
  "retrieved_at": "2026-07-27T14:05:00Z",
  "data_through": "2026-07-26T23:59:59Z",
  "is_fixture": false,
  "schema_version": "1"
}
```

The UI and final report must show:

- Data-through timestamp
- Fixture versus live mode
- Billing knowledge review date
- Applied rule identifiers
- Supported FOCUS version
- Stale or incomplete data warnings

The agent must not claim real-time coverage when provider data is delayed. Missing recent data must not automatically be classified as zero usage or a resolved anomaly.

## 7. Pricing data

When exact current prices are needed, use deterministic pricing adapters backed by official structured sources:

```text
AwsPricingProvider
AzureRetailPricesProvider
GcpCloudBillingCatalogProvider
```

Cache dated snapshots for reproducibility. Agents must not estimate current prices from memory or general web search. Exact live pricing is not required by the fixture-based system and is not implemented.

## 8. Documentation and rule regression tests

Add scenarios that prove version-aware interpretation:

- An older bill uses the rule valid on its usage date.
- A newer bill uses a changed discount or export rule.
- An unsupported future schema is rejected safely.
- Stale knowledge produces a visible warning.
- Missing effective dates prevent a high-confidence conclusion.
- Provider data delay is considered before reporting missing usage.
- A documentation-only change does not alter calculations.
- Every production rule links to an official source.

Context7 is not required by CI. Playwright Test validates freshness labels, source links, fixture/live indicators, and stale-knowledge warnings in the UI. Playwright MCP may be used for optional exploratory QA, but neither Playwright mode is a billing-data source.

## 9. What is built

Delivered: the versioned knowledge directory and rule schema, 51 reviewed rules
citing official sources, the read-only Billing Knowledge MCP server, date-aware rule
and stale-knowledge regression scenarios in `tests/knowledge`, the weekly
documentation-change job, and provenance display in the report and the UI.

Outstanding: provider API and schema version checks, which land with the live
connectors.

