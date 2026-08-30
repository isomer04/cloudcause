# Testing strategy

Why the test suite is shaped the way it is. [`usage.md`](usage.md) has the commands;
this explains the layers and what each one is responsible for.

**AWS, Azure, and Google Cloud accounts are not required** for development, the test
suite, the scored evaluation, or deterministic demo runs. Data source and investigator
selection stay independent: `CLOUDCAUSE_DATA_MODE` chooses where provider data comes
from, while each `InvestigationRequest.agent_mode` chooses deterministic playbooks or
real frameworks. The offline evaluation explicitly requests fixtures plus playbooks,
which costs $0.00 and needs no network.

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

433 tests pass offline in roughly 30 seconds with no keys, no network, and no
Docker. The 15 skips are `tests/persistence`, which needs a PostgreSQL; with
`CLOUDCAUSE_TEST_DATABASE_URL` set the suite is 448 passing and nothing skipped,
which is what CI runs. Layers present:
`tests/unit`, `tests/contract`, `tests/gateway`, `tests/e2e`, `tests/knowledge`,
`tests/mcp`, `tests/persistence`, `tests/rate_limit`, `tests/security`,
`tests/ui`, `tests/worker_api`, and opt-in `tests/live` excluded from automatic
offline CI.

CI runs the offline suite and deterministic scorecard on every push. These prove the
application pipeline, arithmetic, evidence controls, and regression behavior; they do
not claim to measure hosted-model reasoning quality. The Next.js typecheck and build,
PostgreSQL integration, and fixture regeneration are also offline gates.

The separate manual `live-agent-evaluation` workflow uses the installed framework
stack and runs `tests/live` with hosted models. It measures semantic findings, tool-path
participation, evidence grounding, reconciliation, and fallback detection. It requires
`OPENAI_API_KEY` and `GOOGLE_API_KEY` in the protected `live-evaluation` GitHub
environment, spends model tokens, and is intended for model changes and release checks
rather than every pull request.

Outstanding: full Playwright end-to-end suites over the Next.js app. The gateway
contract tests carry that assurance today.

## Running the suites

```bash
uv run pytest tests -q                        # 433 offline, $0, no keys, no Docker
uv run python evaluations/run_evaluation.py   # scored metrics report
make live-evaluate                            # opt-in hosted-model quality gate
```

The 15 skips are `tests/persistence`, which needs the PostgreSQL it stores
investigation history and shared datasets in. Start one and the suite is 448
passing with nothing skipped:

```bash
docker compose -f docker/docker-compose.yml up -d postgres
uv sync --extra postgres
CLOUDCAUSE_TEST_DATABASE_URL=postgresql://cloudcause:cloudcause-local-only@127.0.0.1:5432/cloudcause \
  uv run pytest tests -q
```

Offline CI and the deterministic evaluation run on every push, and a separate
`postgres-storage` job runs the persistence suite against a real PostgreSQL. A separate manual
`live-agent-evaluation` workflow runs the real frameworks with repository secrets and
spends model tokens; it is intentionally not a pull-request gate. Offline CI
regenerates the fixtures and fails if the committed ones differ, so the demo data
cannot drift from its generator.
Full command reference in [usage.md](usage.md); the reasoning behind the
layers is in this document.

