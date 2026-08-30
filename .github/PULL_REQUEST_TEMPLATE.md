<!--
Keep every heading below. If a section has nothing to report, write
"N/A — <one-sentence reason>" rather than deleting the heading.
-->

## Summary

<!-- 1-3 sentences. What was wrong (past tense) and what this PR changes
(present tense), understandable without opening the diff. -->

## Issue

<!-- "Closes #N" — closing keywords are only parsed from the PR body, not the
title. Write "N/A — <reason>" if this PR has no tracking issue. -->

## Changes

<!-- Root cause FIRST for a fix: why did the defect exist, not only what moved.
Then bullets by file or module: what changed and why. -->

## Invariants

<!-- These are the guarantees this repository does not trade away; the reasoning
behind each is in docs/architecture.md and the relevant ADR. Tick what this PR
touched and say how the guarantee still holds; leave the rest unticked. -->

- [ ] **Code owns the numbers** — no model or UI arithmetic added; the UI still only formats gateway values
- [ ] **Agent output is untrusted** — findings still pass evidence-ID validation and reconciliation before publication
- [ ] **Cost does not prove cause** — cost-only data still yields `unexplained_increase` at confidence `<= 0.40`
- [ ] **Cloud access stays read-only** — no mutating tool added; MCP tools remain allowlisted and named `get_`
- [ ] **External text is data** — sanitization preserved; no credential or uploaded row value reaches a log or error
- [ ] **Uploads stay sealed** — raw bytes still discarded; no silent fixture or memory fallback
- [ ] **Execution mode is honest** — live failure still degrades to `partial` with a warning
- [ ] **Knowledge is dated and sourced** — billing rules keep provenance and effective dates, selected by usage date
- [ ] N/A — this PR touches none of the above

## Contract changes

<!-- A change to packages/contracts must update every caller, every transport,
web/lib/types.ts, and the contract tests in the same PR. -->

- [ ] `packages/contracts` changed, and callers, transports, `web/lib/types.ts`, and contract tests are updated here
- [ ] N/A — no contract change

## Decision records

- [ ] A new ADR is included, or an existing one is superseded by a new record (accepted ADRs are immutable)
- [ ] N/A — no decision worth recording

## Testing

<!-- Tick only what was actually run in this PR, and paste the result line.
Never tick a box for a command you did not run. -->

- [ ] `make lint`
- [ ] `make test`
- [ ] `make typecheck`
- [ ] `make build`
- [ ] `make evaluate`
- [ ] Fixtures regenerated twice with no diff on the second run (`fixtures/` changes only)
- [ ] `docker compose -f docker/docker-compose.yml config --quiet` (Compose changes only)

**Live validation:** <!-- "None — offline path only, $0, no keys" is the
expected answer. Only state otherwise if the user explicitly asked for a live
run and supplied credentials. -->

**Regression test added:** <!-- Name the test and what it pins. Behaviour changes
need one. -->

## Screenshots / Demo

<!-- Before/after for anything a reader sees. "N/A — <reason>" otherwise. -->

## Notes for Reviewers

<!-- Pre-existing failures unrelated to this PR, stated explicitly so they are
not attributed to this change. Anything you deliberately left out of scope, and
anything you want a second opinion on. "None observed" is a valid answer. -->
