# Example billing exports

These synthetic files are ready for the **Your data** upload flow. They contain
no real account, subscription, project, or resource identifiers.

| Provider | File | Upload as |
| --- | --- | --- |
| AWS | [`aws-cost-and-usage.json`](aws-cost-and-usage.json) | AWS cost |
| Azure | [`azure-cost-management.json`](azure-cost-management.json) | Azure cost |
| Google Cloud | [`gcp-billing-export.csv`](gcp-billing-export.csv) | GCP cost |

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
investigation. These are intentionally cost-only examples: CloudCause can measure
and reconcile the increase, but it reports the mechanism as unexplained because
no metrics, inventory, audit events, or recommendations were supplied.
