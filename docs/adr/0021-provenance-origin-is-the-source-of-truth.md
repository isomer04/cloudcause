# ADR 0021: `Provenance.origin` is the source of truth, and an upload is never rendered as live

* Status: Accepted
* Date: 2026-09-16
* Scope: `cloudcause_contracts.common` (`DataOrigin`, `Provenance`,
  `reconcile_origin`), the report's `data_origin`, the PDF and markdown renderers
* Records a decision made when uploads landed (ADR 0005) and extended by the live
  connectors (ADR 0015). Supersedes nothing.

## Context

The system began with two kinds of data: fixtures, and a live connector that did not
exist. `Provenance.is_fixture: bool` was enough. Uploads (ADR 0005) introduced a third
kind that the flag cannot express: real data a human handed over, which is neither a
fixture nor something CloudCause verified against a cloud account. Rendered as
"fixture" it looks fake; rendered as "live" it claims a verification that never
happened.

The report, the evidence items, the worker HTTP contract and the fixture manifests all
carry provenance, and the manifests are checked-in JSON that still says `is_fixture`.

## Decision

**`origin` is a three-valued enum - `fixture`, `upload`, `live` - and it is the
source of truth.** Every renderer, the report's `data_origin`, and every "is this
real?" decision reads `origin`. The words shown to a reader are fixed per value:
*Fixture data*, *Your uploaded export*, *Live provider data*.

**`is_fixture` is deprecated and derived, never authoritative.** It stays a declared
field so the worker HTTP contract and the fixture manifests keep validating - the
contracts forbid extra keys, and a computed field would make serialize-then-validate
fail across HTTP. `reconcile_origin` keeps the two in agreement in both directions:
given `origin`, the flag is derived; given only the flag, `origin` is derived. When
both are supplied, **`origin` wins**, because a payload claiming `origin="upload"`
with `is_fixture=True` would otherwise store a contradiction and let uploaded numbers
read as verified fixtures.

**An absent source still says where it came from.** A cost-only upload returns empty
metrics with `origin="upload"` and `source="...-absent"`; a live run returns empty
inventory with `origin="live"` and the same suffix. The honest statement is "this
dataset has no metrics", never "these metrics came from somewhere else".

**A mixed run reports the least verified origin.** When a report combines sources,
`data_origin` resolves in the order `upload`, `fixture`, `live`: if any source was
uploaded the report is an upload report, and only a run with nothing but live data is
a live report.

## Rationale

The word on the report is a claim about verification, and the three origins carry
three different claims. Fixture data was authored. Uploaded data is real but was
never checked against the account it describes. Live data was read from the account
by the operator's own credential. Collapsing any two of those misleads in one
direction or the other.

Keeping the deprecated flag rather than deleting it is the cheaper honesty: the cost
is one validator, the alternative is a breaking change to a contract that crosses
HTTP and to checked-in manifests, for no gain in correctness.

## Alternatives considered

**Add `is_upload` next to `is_fixture`.** Two booleans for three states invites the
fourth, contradictory state.

**Delete `is_fixture` and migrate everything.** Correct in the long run; not worth a
breaking change while the manifests and the worker contract are stable.

**Let the renderer guess from the source name.** Rejected; a string convention is not
a contract.

## Consequences

* New data kinds extend the enum, never add a flag.
* The fixture manifests may keep `is_fixture` indefinitely; `reconcile_origin` reads
  them correctly.
* Enforced by `tests/contract/test_upload_provider.py` (an upload keeps
  `origin="upload"`, including on absent sources, and a report over an upload is an
  upload report) and by `reconcile_origin`'s validator on every `Provenance`.
