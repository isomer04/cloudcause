# CloudCause

**Evidence-grounded multi-cloud cost spike investigator.** It answers one question:
why did AWS, Azure, or Google Cloud spending increase, what evidence identifies the
cause, and what should a human consider doing about it?

[![offline CI](https://github.com/isomer04/cloudcause/actions/workflows/offline-ci.yml/badge.svg)](https://github.com/isomer04/cloudcause/actions/workflows/offline-ci.yml)
[![licence: PolyForm Noncommercial](https://img.shields.io/badge/licence-PolyForm%20Noncommercial%201.0.0-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Next.js 16](https://img.shields.io/badge/Next.js-16-black.svg)

Not another billing dashboard. Deterministic Python measures the money, three agent
frameworks investigate the cause, and every conclusion carries evidence IDs plus a
versioned billing rule with an official source. Read-only by construction: no tool
in the system can delete, stop, scale, or modify a cloud resource.

## Run it offline in two commands

No AWS, Azure, or Google Cloud account. No model API key. No network.

```bash
uv sync && uv run cloudcause-api             # gateway on http://127.0.0.1:8000/docs
cd apps/web && npm install && npm run dev    # UI on http://localhost:3000
```

Pick the `default` scenario and press **Investigate**. Prefer the terminal? One
report, no services: `uv run python evaluations/run_evaluation.py`.

![The investigation brief: data source, question, providers, and the two date
ranges being compared](docs/images/cloudcause-brief.png)

## What one run produces

The demo fixture plants three causes across three clouds plus a quieter fourth. A
real run against it, reproducible offline:

```
Spend         618.66 USD  ->  1038.26 USD      +419.60 USD  (+67.8%)
Attributed    419.60 USD      Unattributed 0.00 USD      reconciled within tolerance
Findings      4               Validation issues 0        Warnings 0
```

![The report: verdict, the reconciliation strip showing 0.00 unattributed, and the
top-ranked cause with its cost impact, confidence, and billing rule](docs/images/cloudcause-report.png)

| Provider | Cause identified | Impact | Confidence |
| --- | --- | --- | --- |
| GCP | Exposed API key drives Cloud Translation usage from unrecognized networks | +161.60 USD | 0.92 |
| AWS | Route change sends S3 traffic through a NAT Gateway after the VPC endpoint is deleted | +126.00 USD | 0.92 |
| Azure | Function App retry loop after a deployment | +103.20 USD | 0.92 |
| AWS | Forgotten sandbox EC2 instance | +28.80 USD | 0.92 |

The four impacts sum to the total change exactly, because attribution is arithmetic
the analytics layer performs and the reconciliation step refuses to publish a report
where they do not balance.

## Measured results

`evaluations/` scores the system against 12 seeded scenarios with known causes,
including four adversarial ones: a pricing change that is not a usage change,
delayed billing data, a partial trailing day, and untagged resources.

| Metric | Result |
| --- | --- |
| Scenarios passed | 12 / 12 |
| Root cause ranked in top 3 | 100% |
| Cost attribution accuracy | 100% |
| Claims backed by evidence | 100% |
| Unsupported claims per run | 0.00 |
| Offline test suite | 305 passed, 1 skipped |
| Cost to reproduce all of it | $0.00 |

## Why it is built this way

**The model never touches the arithmetic.** Cost figures come from
`packages/anomaly_detection` and are passed to the agents as prepared evidence. An
agent can restate an attribution and rank candidates; it cannot compute, inflate, or
invent one. This is the difference between a tool finance can act on and a
plausible-sounding guess.

**A finding must survive validation to be published.**
`packages/evidence_validation` drops or downgrades claims the evidence does not
support, and records the reason in the report. Undated or stale billing knowledge
caps confidence. Cost-only data yields `unexplained_increase` at confidence
`<= 0.40` rather than a fabricated mechanism.

![One finding expanded: nine evidence rows, each with an ID, a typed source URI,
an observation timestamp, and the statement it
supports](docs/images/cloudcause-evidence.png)

**Three frameworks, one per cloud, on purpose.** Google ADK coordinates and owns
GCP, AWS Strands Agents owns AWS, Microsoft Agent Framework owns Azure. They meet at
an HTTP contract, so a framework is a replaceable implementation detail rather than
the architecture. Each specialist falls back to deterministic playbooks and reports
`partial` if its SDK errors, so a quota limit degrades a run instead of losing it.

**Two tool mechanisms, chosen rather than defaulted.** Native tool calling for
in-process deterministic helpers; MCP for the external evidence boundary, where
read-only allowlisting and provenance matter. All 13 MCP tools are named `get_` and
the servers expose no other verb. The 5 native tools add the two names that are not
reads — `recalculate_attribution`, which asks the deterministic layer to restate a
figure, and `record_finding`, which writes into the report — and neither reaches a
cloud account.

**Billing rules are versioned and date-aware.** 51 rules under `knowledge/`, each
citing official provider documentation, selected by the usage date being explained
so a July rule is never applied retroactively to a June bill. A weekly CI job diffs
the upstream docs and opens an issue when a source changes; it never rewrites a rule
automatically.

**One comparison engine, not three.** Provider exports are normalized to a FOCUS 1.4
projection before anything is compared, so adding a cloud is a parser rather than a
second analytics stack.

The service topology, the repository layout, and the contracts are in
[docs/architecture.md](docs/architecture.md).

## Testing

```bash
uv run pytest tests -q                        # 305 tests, offline, $0, no keys
uv run python evaluations/run_evaluation.py   # scored metrics report
```

Three offline CI jobs run on every push, and one of them regenerates the fixtures and
fails if the committed ones differ, so the demo data cannot drift from its generator.
Full command reference in [docs/usage.md](docs/usage.md); the reasoning behind the
layers is in [docs/testing-strategy.md](docs/testing-strategy.md).

## Running with live agents

The offline default is a real run, not a placeholder: deterministic playbooks produce
the findings, no key is read, and no request leaves the machine. Live mode swaps those
playbooks for the three frameworks on the same fixture data.

```bash
uv sync --extra live
CLOUDCAUSE_AGENT_MODE=live GOOGLE_GENAI_USE_ENTERPRISE=FALSE uv run cloudcause-api
```

```powershell
# Windows PowerShell
$env:CLOUDCAUSE_AGENT_MODE="live"; $env:GOOGLE_GENAI_USE_ENTERPRISE="FALSE"; uv run cloudcause-api
```

Keys belong in `.env` as `OPENAI_API_KEY` and `GOOGLE_API_KEY`; real environment
variables always win over the file.

**A key in `.env` does not enable live mode.** `CLOUDCAUSE_AGENT_MODE` defaults to
`stub`, so a gateway started without it investigates deterministically and never calls
a model, which is the usual reason nothing shows up in a provider's request logs.
Check before you investigate rather than after:

```bash
curl -s localhost:8000/health     # agent_mode: live
```

Every run records which path it took, so a finished report answers the same question
after the fact: findings and the report carry `agent_mode`, and the exported Markdown
prints it in the header. Confidence is not the tell — it comes from the rule that was
matched, and the deterministic run above reaches 0.92 without a model.

![A live Azure run: the trail shows 35 rows normalized to FOCUS 1.4, a deterministic
+78.40 comparison, then the Microsoft Agent Framework specialist returning one finding
that reconciles to the measured change](docs/images/cloudcause-live-run.png)

The provider's own request log is the outside view of the same run, and it shows the
division of labour from [ADR 0002](docs/adr/0002-deterministic-arithmetic.md) as
executed calls: the agent asks for the plan, the candidates, and the evidence, then
records a finding. It never asks for a total, because the total was already computed in
Python before the agent was invoked.

![The OpenAI request log for one investigation: get_investigation_plan,
get_anomaly_candidates, get_candidate_evidence, then record_finding, all on the same
investigation id](docs/images/cloudcause-live-tool-calls.png)


## Security posture

Read-only by construction: all 13 MCP tools that cross the evidence boundary are
allowlisted and named `get_`, the only write in the system is `record_finding` putting
a finding into the report, and nothing can delete, stop, scale, or modify a cloud
resource. Credentials never enter
prompts, logs, reports, or model requests. Uploaded bytes are parsed from the request
stream and discarded — nothing raw reaches disk and no row value is ever logged, which
`tests/security` enforces. Provider text is treated as untrusted input rather than
instructions. Full posture and the read-only roles a live connector would need are in
[docs/architecture.md](docs/architecture.md#8-security-and-guardrails).

## Scope and honest limits

* **The gateway is unauthenticated.** A public deployment that accepts uploads needs
  authentication and per-user isolation first. Multi-tenancy is out of scope and the
  UI does not imply otherwise.
* **No live cloud connectors yet.** `CLOUDCAUSE_DATA_MODE=live` raises a clear error
  rather than pretending. The three working input paths are demo fixtures, 12 seeded
  scenarios, and your own uploaded cost export.
* **A cost export alone cannot evidence a cause**, and the system says so instead of
  guessing. Adding metrics, audit events, inventory, or provider recommendations for
  the same period is what raises a finding to a specific mechanism.
* **SSE progress assumes a single gateway process.** Horizontal scaling needs sticky
  sessions or Redis fan-out; the polling endpoint already works across replicas.

## Documentation

| Document | What it covers |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | The system shape: services, tool strategy, contracts, security posture |
| [docs/usage.md](docs/usage.md) | Runtime modes, uploads, history backends, containers, every test command |
| [docs/adr/](docs/adr/) | Why individual forks in the road were taken, one file per decision |
| [docs/billing-knowledge.md](docs/billing-knowledge.md) | The versioned rule store, its MCP server, and the update workflow |
| [docs/testing-strategy.md](docs/testing-strategy.md) | Why the suite is layered the way it is |
| [fixtures/README.md](fixtures/README.md) | The synthetic data shapes and the generator |
| [evaluations/README.md](evaluations/README.md) | Scenarios, expectations, and how scoring works |

## Attribution and licence

CloudCause is released under the
[PolyForm Noncommercial License 1.0.0](LICENSE).

**Read, run, fork, modify, and share it freely for any noncommercial purpose** —
personal study, hobby projects, research, teaching, and use by charities,
educational institutions, public research bodies, and government. **Commercial use
requires a separate licence**; open an issue to ask about one.

To be precise about a term that gets used loosely: this is a *source-available*
licence, not an Open Source licence as the
[Open Source Initiative defines it](https://opensource.org/osd), because clause 6 of
the Open Source Definition forbids restricting a field of endeavour and this licence
restricts commercial use. Versions published before this change remain under the MIT
licence they were released with.

The three agent frameworks were learned from Ed Donner's course examples. That
material is not redistributed here and nothing under `packages/`, `services/`, or
`apps/` is derived from it. Provider billing behaviour is summarized from the
official documentation linked in each rule under `knowledge/`; external source
content is paraphrased for licensing compliance.

Dependencies keep their own licences, which are unaffected by this one.
