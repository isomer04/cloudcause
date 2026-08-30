# ADR 0012: The cloud deployment is single-process; the split stays local

* Status: Accepted
* Date: 2026-08-15
* Scope: `terraform/gcp`, `docker/`, `cloudbuild.yaml`

## Context

[ADR 0003](0003-framework-per-cloud.md) puts one agent framework per cloud, each
its own service behind an HTTP contract, with `CLOUDCAUSE_ORCHESTRATOR_MODE` and
`CLOUDCAUSE_WORKER_MODE` selecting `inprocess` or `http`. Both transports are
covered by `tests/worker_api`, and `docker/docker-compose.yml` runs the
distributed topology locally.

That leaves an open question ADR 0003 does not answer: which transport the hosted
demo should use. The four Python entry points are one image launched four ways,
so `http` would mean four Cloud Run services plus the frontend, and `inprocess`
would mean two.

The deployment is a portfolio demo on trial credits. Its readers arrive from a
link, click once, and judge what they see.

## Decision

**Cloud Run runs two services: the gateway and the frontend.** The gateway holds
the ADK orchestrator and both framework workers in process. `terraform/gcp` sets
both modes to `inprocess`.

**The distributed topology is not removed, only not hosted.** Compose still runs
five processes over `http`, `tests/worker_api` still covers both transports, and
flipping either variable is a config change rather than a rewrite.

**Images are built by Cloud Build and passed to Terraform by tag.** Terraform
describes infrastructure and does not build anything, so `apply` needs no local
Docker daemon.

**Exactly three GCP APIs are enabled**: Cloud Run, Artifact Registry, Cloud
Build. Model keys are plain environment variables on the revision, not Secret
Manager.

## Rationale

A cold start is the whole argument. In `http` mode a first request with
everything scaled to zero starts four containers in sequence, each importing an
agent framework. In `inprocess` mode it starts one. A reviewer who waits through
the first version has already formed an opinion about the project.

The distributed design is legible from the repository — ADR 0003, the two
transport implementations, and the contract tests over both — and none of that
depends on how many containers GCP happens to be running. Hosting five services
adds three service-to-service wirings and no additional evidence of the claim.

Splitting build from deploy is what the project's first Terraform already did: it
took an image as a variable. Building inside `terraform apply` through the
`kreuzwerker/docker` provider put multi-gigabyte image pushes on a home
connection, required Docker locally, and made image builds part of Terraform
state.

Three APIs is a deliberate ceiling. Secret Manager and per-service identities are
the right answer for a deployment other people can reach; they are four more
resources to explain for one that nobody can.

## Alternatives considered

**Five Cloud Run services, `http` transport.** The topology ADR 0003 describes,
deployed as written. Rejected for the cold-start chain, and because a warm
instance on every hop to avoid it costs five idle containers to demonstrate
something the repository already shows. This is the alternative to revisit first
if a worker ever needs to scale on its own.

**One Cloud Run service, four containers as sidecars.** Cloud Run supports
multiple containers per service sharing a network namespace, which would exercise
`http` over `localhost` behind a single cold start. Rejected as untested against
this codebase and more Terraform than either option, for a benefit that is
presentational at current scale.

**Per-service images via `uv sync --package`.** The workers need 52 and 66 of the
99 production packages, so separate images would cut roughly a third to a half of
each worker's payload. Rejected: it trades one build for four to optimise pull
time, and a warm instance removes the problem it solves.

**Secret Manager for model keys.** Rejected for this deployment only. The keys
sit in the revision spec and in Terraform state, which is acceptable while the
project is one person's and unacceptable the moment it is not.

## Consequences

Accepted:

* The hosted demo does not exercise the `http` transport. Compose and
  `tests/worker_api` are the only places that path runs, so a regression in it
  fails in CI rather than in production, which is the correct order.
* One container holds the gateway, the orchestrator, both workers, and the MCP
  servers it spawns as child processes. Memory is the binding constraint, not
  CPU: `api_memory` defaults to 2Gi and should not be lowered for live mode.
* Investigation history and rate-limit buckets are per-instance memory, so
  `api_max_instances` stays at 1 until a shared database and a Redis backend are
  configured. Scaling out silently would divide the live-run quota per instance.
* `terraform.tfstate` holds any key passed with `-var` in cleartext and is
  gitignored, along with `*.tfvars`. Rotating a key means re-applying.
* A new revision requires a new `image_tag`. Cloud Run compares image strings, so
  rebuilding `:latest` deploys nothing.
