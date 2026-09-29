# Architecture Decision Records

One file per decision that would otherwise be re-litigated in a code review or an
interview. Numbered, immutable once accepted: a reversal is a new ADR that
supersedes the old one rather than an edit to it.

[`../architecture.md`](../architecture.md) says what the system is. These say why a fork in the road
was taken and what was given up.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-streaming-transport.md) | Server-Sent Events for browser progress, stdio for MCP | Accepted |
| [0002](0002-deterministic-arithmetic.md) | Deterministic code owns every number the report publishes | Accepted |
| [0003](0003-framework-per-cloud.md) | One agent framework per cloud, meeting at an HTTP contract | Accepted |
| [0004](0004-native-tools-and-mcp.md) | Native tool calling inside, MCP at the evidence boundary | Accepted |
| [0005](0005-uploads-are-sealed-server-parsed-datasets.md) | An upload is a sealed, server-parsed dataset addressed by id | Accepted |
| [0006](0006-cost-only-data-cannot-name-a-cause.md) | A cost export alone may never name a cause | Accepted |
| [0007](0007-validate-and-reconcile-agent-findings.md) | Agent findings are untrusted until validated and reconciled | Accepted |
| [0008](0008-offline-first-evaluation-and-graceful-fallback.md) | Offline evaluation is the default; live agents degrade visibly | Accepted |
| [0009](0009-materiality-leaves-a-residual-and-the-residual-is-published.md) | Materiality leaves a residual, and the residual is published | Accepted |
| [0010](0010-confidence-is-derived-not-capped.md) | Confidence is derived from evidence, never reported from a ceiling | Accepted |
| [0011](0011-no-langsmith-tracing-is-otlp-or-nothing.md) | LangSmith is not adopted; agent tracing, if added, arrives over OTLP | Accepted |
| [0012](0012-cloud-deployment-is-single-process.md) | The cloud deployment is single-process; the split stays local | Accepted |
| [0013](0013-actor-attribution-is-an-allocation-estimate.md) | Actor attribution is an allocation estimate, not a billed fact | Accepted |
| [0014](0014-cloudcause-never-holds-cloud-credentials.md) | CloudCause never holds a customer's cloud credentials | Accepted |
| [0015](0015-live-connectors-are-local-only.md) | Live connectors are local-only; the deployed service still holds no credentials | Accepted |
| [0016](0016-live-fetches-are-cached-and-sdk-logs-are-muted.md) | A live connector fetches once per investigation and silences SDK debug logs | Accepted |
| [0017](0017-coverage-ends-at-the-last-complete-day.md) | Coverage ends at the last complete day, never at the requested end date | Accepted |
| [0018](0018-model-spend-is-governed-in-layers-and-fails-closed.md) | Model spend is governed in layers, and the limiter fails closed | Accepted |
| [0019](0019-history-stores-hashed-identifiers-and-never-fails-a-run.md) | Durable history stores hashed identifiers, and persistence never fails a run | Accepted |
| [0020](0020-billing-knowledge-is-dated-cited-and-reviewed.md) | Billing knowledge is dated, cited, and reviewed, never scraped | Accepted |
| [0021](0021-provenance-origin-is-the-source-of-truth.md) | `Provenance.origin` is the source of truth; an upload is never rendered as live | Accepted |

## Hiring-manager reading path

For a short architecture review, these five records carry the main engineering
story:

1. [0002](0002-deterministic-arithmetic.md): why a language model never owns a
   financial number.
2. [0003](0003-framework-per-cloud.md): why three frameworks are isolated behind one
   contract.
3. [0004](0004-native-tools-and-mcp.md): why the system could work without MCP but
   uses it for the read-only evidence boundary.
4. [0007](0007-validate-and-reconcile-agent-findings.md): how an agent claim becomes
   a publishable, auditable finding.
5. [0008](0008-offline-first-evaluation-and-graceful-fallback.md): how the system is
   tested without hiding live-agent failures.
