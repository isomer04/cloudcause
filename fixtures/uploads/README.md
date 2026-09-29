# Example billing exports

These synthetic files are ready for the **Your data** upload flow. They contain
no real account, subscription, project, or resource identifiers.

| Provider | File | Upload as |
| --- | --- | --- |
| AWS | [`aws-cost-and-usage.json`](aws-cost-and-usage.json) | AWS cost |
| Azure | [`azure-cost-management.json`](azure-cost-management.json) | Azure cost |
| Google Cloud | [`gcp-billing-export.csv`](gcp-billing-export.csv) | GCP cost |
| AWS | [`aws-cloudtrail.json`](aws-cloudtrail.json) | AWS audit |

The Azure and Google Cloud files cover these two seven-day periods:

- Baseline: `2026-07-06` through `2026-07-12`
- Investigation: `2026-07-13` through `2026-07-19`

The AWS file has one hourly bucket for July 19, so its complete-day coverage is
reported through July 18. Its records still preserve the intended `$140.00`
investigation-period increase. The Azure and Google Cloud example resources cost
`$10.00` per day in the baseline and `$30.00` per day in the investigation period,
also producing a `$140.00` increase.

In the UI, choose **Your data**, select one provider, and drop its file into the
cost-export field. Seal the dataset, use the dates above, and open the
investigation. The three cost files are intentionally cost-only examples:
CloudCause can measure and reconcile the increase, but it reports the mechanism as
unexplained because no metrics, inventory, audit events, or recommendations were
supplied.

## The CloudTrail example

`aws-cloudtrail.json` is a raw AWS CloudTrail export - the provider's own
`{"Records": [...]}` envelope, not CloudCause's `{"items": [...]}` shape. It is
detected by content and mapped on ingest, so no conversion step is needed. Upload
it in the **audit** slot alongside `aws-cost-and-usage.json`.

It covers the same two weeks and names the same instance as the AWS cost file, so
the pair supports a cost breakdown grouped by `actor`. Two roles use the instance:
`batch-etl` every night in both weeks, and `ml-training` three times a day from
July 13 - which is where the increase lands.

Three things about it are worth reading the file for:

- **Every record is an `AssumedRole` with a different session name.** The ARNs end
  in `etl-0706`, `train-0713-0` and so on. Attribution keys on the session
  issuer's role name instead, so these collapse to two actors rather than
  thirty-five.
- **CloudTrail names the instance by full ARN; the CUR names it `i-example0000000001`.**
  The join handles that mismatch; an exact-match join would attribute nothing.
- **It carries no token counts**, so shares fall back to invocation count and the
  response says so in a warning. That is deliberate: invocation count is a weak
  proxy for spend, and the estimate is published with its method attached rather
  than presented as a measurement. See
  [ADR 0013](../../docs/adr/0013-actor-attribution-is-an-allocation-estimate.md).
