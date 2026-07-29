# Testing strategy

Why the test suite is shaped the way it is. [`usage.md`](usage.md) has the commands;
this explains the layers and what each one is responsible for.

**AWS, Azure, and Google Cloud accounts are not required** for development, the test
suite, the scored evaluation, or the public demo. Two independent switches make that
work: `CLOUDCAUSE_DATA_MODE` chooses where provider data comes from and
`CLOUDCAUSE_AGENT_MODE` chooses deterministic stubs or real frameworks, so data realism
and model spend never move together. The offline default is fixtures plus stubs, which
costs $0.00 and needs no network.

## 1. Local prerequisites

Required:

- Python and `uv`
- Git
- Synthetic JSON/CSV fixture data

Optional:

- Node.js for `npx`-based MCP servers and the Next.js frontend
- Docker Desktop and PostgreSQL for the full local stack
- OpenAI and Gemini API keys for live-agent integration tests

Not required:

- AWS account or AWS credentials
- Azure subscription or service principal
- GCP project, billing account, or service account
- Real cloud infrastructure

## 2. Provider adapter boundary

Agents access provider data through interfaces rather than importing cloud SDK clients directly into business logic.

```python
class AwsDataProvider(Protocol):
    async def get_costs(self, period: DateRange) -> list[CostRecord]: ...
    async def get_resources(self) -> list[CloudResource]: ...
    async def get_metrics(self, resource_id: str) -> list[Metric]: ...
    async def get_audit_events(self, period: DateRange) -> list[AuditEvent]: ...
```

Paired adapters:

```text
FixtureAwsDataProvider    / LiveAwsDataProvider
FixtureAzureDataProvider  / LiveAzureDataProvider
FixtureGcpDataProvider    / LiveGcpDataProvider
```

Configuration injects the appropriate adapter. Agent prompts and report generation must behave identically in fixture and live modes.

## 3. Test layers

### Deterministic unit tests

No agents, network access, cloud credentials, or model keys. Test normalization, period comparison, grouping, materiality thresholds, cost reconciliation, and anomaly ranking.

### Provider contract tests

Run every fixture adapter against the same behavioral contract expected from its future live adapter. Validate provider names, resource identifiers, timestamps, currencies, and FOCUS-normalized fields.

### Worker API tests

Run the ADK, MAF, and Strands services independently with stub agents. Validate `/health`, investigation submission, status retrieval, schemas, timeouts, and error responses.

### Framework integration tests

Run real frameworks against fixture tools with small hosted models. Assert semantic properties instead of exact prose:

- Correct provider and anomaly category
- Expected resource or service in the top findings
- Minimum evidence count
- Confidence within `[0, 1]`
- Cost attribution within tolerance
- No unsupported resource identifiers

These tests are opt-in and should be excluded from normal offline CI runs.

### Offline end-to-end test

Run the API, ADK orchestrator, Strands worker, MAF worker, evidence validator, and report generator. Verify that all three workers participate, planted causes are found, evidence IDs resolve, costs reconcile, and no mutating tool is available or called.

## 4. Model and cost strategy

- Stub mode costs `$0` and is the default.
- Live-agent tests use small/fast models and strict token limits.
- Cache fixture reads and deterministic intermediate results.
- Set maximum calls, retries, runtime, and spend per investigation.
- Do not run live-agent tests on every file save or pull request.
- Local-model support through an OpenAI-compatible server is not implemented and is not required.

## 5. What the suite covers today

305 tests pass offline in roughly 20 seconds with no keys and no network, plus one
PostgreSQL test that skips itself unless a database is reachable. Layers present:
`tests/unit`, `tests/contract`, `tests/gateway`, `tests/e2e`, `tests/knowledge`,
`tests/mcp`, `tests/persistence`, `tests/security`, `tests/ui`, and opt-in
`tests/live` excluded from CI.

CI runs three offline jobs on every push: lint plus the full suite plus the scored
evaluation, the Next.js typecheck and production build, and the same migrations and
SQL against a real PostgreSQL service container. It also regenerates the fixtures and
fails if the committed ones differ, so the demo data cannot drift from its generator.

Outstanding: full Playwright end-to-end suites over the Next.js app. The gateway
contract tests carry that assurance today.

