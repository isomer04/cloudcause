# ADR 0016: A live connector fetches once per investigation and silences SDK debug logs

* Status: Accepted
* Date: 2026-09-16
* Scope: `packages/providers/live.py`, `tests/unit/test_live_cache_and_logging.py`
* Extends [ADR 0015](0015-live-connectors-are-local-only.md). Supersedes nothing.

## Context

Running the first real investigation through the AWS connector showed two things
ADR 0015 had not accounted for.

**One investigation asks for costs three times.** The orchestrator calls
`get_costs` to build candidates, the worker calls `get_bundle` to build its context,
and the MCP cost tool calls `get_costs` again when an agent asks. Each adapter
instance is built fresh by the registry, so each call was a full fetch. On AWS that is
roughly three rounds of Cost Explorer calls per run, at about a cent each, for data
that is daily and cannot have changed in the seconds between them. On Azure it is
three rounds against an API that rate-limits aggressively.

**The SDKs log more than this module does.** ADR 0015's rule is that nothing from a
provider reaches a log line, and `live.py` keeps it by logging only class names and
counts. But `botocore` logs request parameters and partial signatures at DEBUG,
`azure-core` logs request URLs, and `google-auth` logs token refreshes. A developer
who runs with the root logger at DEBUG to chase an unrelated problem would get all of
that in the same stream. The security test for ADR 0015 could not catch it, because
it stubs the fetch and the SDK never runs.

## Decision

**The raw response is cached per process, keyed by provider, identifier and
window, for fifteen minutes.** The identifier is the subscription id for Azure, the
billing table for GCP, and the `AWS_PROFILE` for AWS - the one thing that can change
which account boto3's default chain resolves to without a restart. Callers that arrive
together wait on one lock per key and share the result. A failed fetch is not cached.
Fifteen minutes covers an investigation with room to spare and is short enough that a
re-run after a provider catches up sees new data. The cache holds only the operator's
own data in the operator's own process, so it needs no tenancy. Expired entries are
removed eagerly and the cache is capped at 128 entries so unique windows cannot grow
process memory without bound.

**The first live fetch raises the SDK loggers to INFO, once.** `boto3`, `botocore`,
`urllib3`, `azure`, `msal`, `httpx`, `httpcore` and `google` are set to INFO if they
are unset or below it. A logger a developer has already set stricter is left alone.
This happens inside the connector rather than at process start so a fixture run, which
never imports an SDK, is untouched.

## Rationale

Caching in the adapter rather than in the three callers keeps the adapter boundary
honest: the orchestrator, worker and tool go on treating the provider as a plain
source, and a fixture or upload adapter needs no cache because it has no cost. Caching
the raw response rather than the mapped records means the mapper still runs on every
call, which is cheap, deterministic, and keeps one code path.

Muting loggers is blunt, and it is the right bluntness. The alternative - a filter that
scrubs credentials out of SDK log records - has to know every SDK's log format and be
right forever. Raising the level asks nothing of the SDKs. What is lost is SDK-level
debugging of a live call, which a developer can get back by setting that logger to
DEBUG explicitly *after* the first fetch, on purpose.

## Alternatives considered

**Pass the bundle from the orchestrator to the worker.** Would remove one of the
three fetches, but the worker is a separate HTTP service in the split topology and the
bundle is not part of its request contract. Changing the contract for a local-only
feature is the wrong trade.

**Cache in the registry, one adapter per process.** Adapters carry settings and a
boundary; sharing instances across requests would make them stateful in a way the
fixture and upload adapters are not.

**Leave logging to the developer.** Rejected. ADR 0015 promised that nothing from the
provider reaches a log line, and a promise that depends on nobody ever enabling DEBUG
is not a promise.

## Consequences

* A second investigation over the same window within fifteen minutes makes no cloud
  call. `clear_live_cache()` exists for tests, and the test suite empties the cache
  before every test.
* Cost Explorer spend per investigation drops to one round of calls.
* SDK debug output is unavailable in live mode unless re-enabled deliberately.
* Enforced by `tests/unit/test_live_cache_and_logging.py`: one fetch across three
  adapter instances, keying by window and identifier, TTL expiry, concurrent callers
  sharing one call, failures not cached, and a botocore DEBUG record not reaching
  `caplog` after a live fetch.
