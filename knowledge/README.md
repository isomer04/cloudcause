# Billing knowledge

Operational data says what happened in an account. Billing knowledge says how to
interpret it: what drives a charge, when a rule took effect, how delayed the data
is, which export schema and API version are supported, and where the official
source is.

Rules are selected by the **usage date under investigation**, never by "now". A
rule that took effect after the billing period is never applied to it.

## Layout

```text
aws/    nat-gateway, cost-explorer, data-freshness, savings-plans, service-cost-drivers
azure/  cost-management, functions, reservations, api-versions, service-cost-drivers
gcp/    billing-export, committed-use-discounts, network-egress, data-freshness, service-cost-drivers
focus/  1.4 (the pinned specification version)
monitored_sources.yaml    official channels the weekly check watches
sources_baseline.json     content hashes, updated only after human review
```

## Rule shape

```yaml
rules:
  - id: aws-nat-gateway-data-processing     # stable, cited in every report
    provider: aws                            # aws | azure | gcp | focus
    rule_type: cost_driver                   # cost_driver | data_freshness | export_schema
                                             # api_deprecation | pricing_source | billing_change
                                             # focus_version
    title: NAT Gateway hourly and data processing charges
    service: nat-gateway
    valid_from: 2025-01-01                   # required for a high-confidence conclusion
    valid_to: null                           # null means still in effect
    reviewed_at: 2026-07-27                  # staleness is measured from here
    schema_version: "1"
    summary: >
      What the charge actually is, in plain language.
    cost_drivers: [hourly_gateway_charge, processed_data]
    investigation_checks: [compare_bytes_processed, inspect_route_changes]
    matches:
      services: [Amazon Virtual Private Cloud, NAT Gateway]
      categories: [nat_gateway_misroute]     # strongest match signal
    source:
      type: official_documentation           # official_documentation | official_api
                                             # release_notes | specification
      url: https://docs.aws.amazon.com/vpc/latest/userguide/nat-gateway-pricing.html
      updated_at: 2025-11-04
    confidence: supported                    # supported | provisional | deprecated
    data: {}                                 # rule-type specific payload
```

Selection order: a matching investigation category beats an exact service name,
which beats a service-name pattern. With no service or category, the provider's
generic rule applies and the answer says so.

## Rules the store enforces

* No rule effective on the usage date means no confident interpretation.
* A missing `valid_from` caps confidence and raises a warning.
* A review older than `CLOUDCAUSE_KNOWLEDGE_REVIEW_MAX_AGE_DAYS` (default 180)
  marks the rule stale, which is visible in the report and the UI.
* FOCUS is pinned to 1.4. Any other version is rejected, not parsed.
* Every rule must carry an official source URL, a summary, and a review date.
  `tests/knowledge/test_rule_regressions.py` fails the build otherwise.

## Adding or changing a rule

1. The weekly job (`.github/workflows/docs-change-check.yml`) reports that a
   monitored source changed. It never edits a rule.
2. A human reads the diff and decides whether it is editorial, regional,
   preview-only, a deprecation, a schema change, or a real billing-rule change.
3. If behaviour changed, add a **new** rule with the correct `valid_from` and set
   `valid_to` on the old one. Do not rewrite history: older bills keep the older
   rule.
4. Update fixtures, scenarios, and regression tests in the same pull request.
5. Record the review date and run `scripts/check_provider_docs.py --update-baseline`.
