# ADR 0018: Model spend is governed in layers, and the limiter fails closed

* Status: Accepted
* Date: 2026-09-16
* Scope: `packages/rate_limit`, `Settings` (the `live_*`, `*_max_concurrency`,
  `*_requests_per_minute`, `ai_retry_*` and `rate_limit_backend` fields),
  `api/main.py`
* Records the design the rate-limiting package implements. Supersedes nothing.

## Context

The gateway is public and unauthenticated (ADR 0014). A live investigation spends
the deployment's model keys, and the keys can be spent by anyone who reaches the
endpoint. Two model providers throttle on different axes - concurrent requests and
requests per minute - and their quotas differ by account tier. And three agents race
inside one investigation, so a limit sized per provider starves two of them: at a
call budget of 12 the AWS agent consumed everything and the ADK and MAF agents fell
back to playbooks.

## Decision

**Four layers, each sized to a different question.**

1. **Admission** at `POST /investigations`, live requests only. Two token buckets are
   checked in order: per client, then deployment-wide. A single noisy client cannot
   exhaust what other clients would get, and the global bucket still caps aggregate
   starts. Client identity is a hashed peer address, salted with
   `CLOUDCAUSE_ID_HASH_SALT`; proxy headers are trusted only when
   `CLOUDCAUSE_TRUST_PROXY_HEADERS` says so.
2. **Concurrency** inside the process: `max_concurrent_live_investigations` with a
   bounded queue wait. Defence in depth for a single process, and explicitly not a
   distributed quota.
3. **Outbound quota** around every model call: one permit per provider family
   (`openai`, `gemini`, `gemini-summary`) covering both in-flight concurrency and
   requests per minute. Acquired at the single model-call boundary in each agent,
   never around a whole agent run, which issues many SDK requests internally.
4. **Retries**: bounded, jittered, capped in wall time, for throttling and transient
   errors only.

**One agent-call budget per investigation, shared by every provider's agent.**
`max_agent_calls` is sized for the three-provider default with retry headroom, and
`max_agent_seconds` bounds queueing as much as reasoning, because a throttled agent
spends most of its time waiting for a permit.

**The limiter fails closed.** A backend error - Redis unreachable, a corrupt bucket -
denies the request with a `Retry-After`, never surfaces as a bare 500, and never lets
the request proceed unbounded. `fail_closed` wraps both admission and outbound checks.

**Memory by default, Redis before scaling.** The in-process backend is correct for one
replica, which is what the Cloud Run demo runs (ADR 0012). Redis is required before the
gateway or the workers go past one replica, and the gateway refuses to start with Redis
and no hash salt.

## Rationale

The layers answer different questions and cannot be collapsed. Admission protects the
key from strangers; concurrency protects the process from itself; the outbound quota
protects the provider account from a 429 storm; retries absorb what the quota cannot
predict. Removing any one of them moves its failure to the next layer, where it is
more expensive.

Failing closed is the only defensible default for a limiter that guards money. A
limiter that fails open is a limiter that stops working exactly when the backend is
under stress, which is when it is needed.

## Alternatives considered

**A single global rate limit.** Would either starve legitimate clients or let one
client take everything, and says nothing about provider quotas.

**Limit per agent run rather than per model call.** Rejected because the frameworks
issue an unpredictable number of SDK requests per run; the model call is the only
boundary that maps to what the provider meters.

**Fail open when the backend is down.** Rejected; see Rationale.

## Consequences

* Every limit is a `Settings` field with a documented default, sized for free-tier
  quotas. Deployments with higher tiers raise them; nothing in code assumes a tier.
* A denied request carries `Retry-After` and a reason, and the UI reads
  `live_agents_available` so it never offers a path the deployment cannot walk.
* Enforced by `tests/rate_limit` (token buckets against a fake clock, admission
  ordering, fail-closed translation) and `tests/unit/test_live_limits.py`.
