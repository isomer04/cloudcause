# Evaluations

Seeded scenarios with expected findings, scored semantically. Model wording is
never compared: the assertions are provider, category, resource, cost attribution
within tolerance, evidence count and sources, confidence range, and rule
citations.

```bash
uv run python evaluations/run_evaluation.py     # metrics report, non-zero exit on failure
uv run pytest tests/e2e -q                      # the same scoring, per scenario
```

To also produce structured and human-readable reports:

```bash
uv run python evaluations/run_evaluation.py \
  --json-output reports/evaluation-results.json \
  --markdown-output reports/evaluation-results.md
```

## Coverage

| Scenario | Provider | Expected category |
| --- | --- | --- |
| `aws-nat-gateway-misroute` | AWS | `nat_gateway_misroute` |
| `aws-unexpected-ai-inference` | AWS | `ai_inference` |
| `aws-cross-region-transfer` | AWS | `cross_region_transfer` |
| `aws-missing-ownership-tags` | AWS | `untagged_resources` |
| `aws-delayed-billing-data` | AWS | none: a warning, not a finding |
| `aws-cost-only-unexplained` | AWS | `unexplained_increase` at confidence `<= 0.40` |
| `azure-functions-retry-loop` | Azure | `functions_retry_loop` |
| `azure-unattached-disk` | Azure | `unattached_storage` |
| `azure-idle-database` | Azure | `idle_database` |
| `gcp-compromised-api-key` | GCP | `api_key_abuse` |
| `gcp-forgotten-vm` | GCP | `idle_compute` |
| `gcp-kubernetes-autoscaling` | GCP | `kubernetes_autoscaling` |
| `gcp-pricing-change-not-usage` | GCP | `pricing_change`, and not a usage cause |

The multi-cloud `default` scenario in `fixtures/` covers three simultaneous
causes and is asserted by `tests/e2e/test_offline_end_to_end.py`.

## Metrics reported

* Scenarios passed
* Correct root cause in the top three
* Cost-attribution accuracy inside tolerance
* Share of findings citing both evidence and a versioned rule
* Unsupported claims per run (validation errors)
* Latency per investigation
* Model cost, which is `$0.00` in stub mode

## Scenario format

`scenarios/<id>.yaml` describes flat background spend, one or more spikes with a
start date, plus the inventory, metrics, audit events, and recommendations an
investigator should find. The generator in
`packages/providers/.../scenarios.py` expands it deterministically into the same
`ProviderDataBundle` the fixture and live adapters produce, so the gateway,
orchestrator, workers, and this harness all rebuild identical data from the
scenario id alone.

`expected_findings/<id>.yaml` states what a correct investigation must conclude:

```yaml
scenario_id: aws-nat-gateway-misroute
expect_findings: true
expect_reconciled: true
top_finding:
  provider: aws
  category: nat_gateway_misroute
  resource_id: nat-0ab12cd34ef56789a
  cost_increase: 126.0
  cost_tolerance: 0.02
  min_confidence: 0.6
  min_evidence: 4
  required_evidence_sources: [cost, usage, metric, audit, recommendation]
  required_rule_ids: [aws-nat-gateway-data-processing]
```

Negative expectations are first-class: `expect_findings: false` with
`expect_warnings_containing` is how the delayed-billing-data scenario proves
CloudCause reports missing data instead of inventing a saving.

## Adding a scenario

1. Write `scenarios/<id>.yaml` and `expected_findings/<id>.yaml`.
2. Run `uv run python evaluations/run_evaluation.py`.
3. If the cause is a new waste pattern, add a playbook to the provider service and
   a versioned rule under `knowledge/`. The harness fails on a missing rule
   citation, which is the point.
