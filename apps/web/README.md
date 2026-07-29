# CloudCause web (Next.js)

The Next.js frontend. It renders investigations, evidence, and provenance from
the FastAPI gateway and holds no investigation logic of its own: no cost math,
no rule lookups, no agent calls. If a number appears here, the gateway computed
it.

## Run it

The gateway has to be running first.

```bash
uv run cloudcause-api                 # http://127.0.0.1:8000

cd apps/web
npm install
npm run dev                           # http://localhost:3000
```

Point it somewhere else with `CLOUDCAUSE_API_URL`:

```bash
CLOUDCAUSE_API_URL=http://127.0.0.1:8010 npm run dev
```

Checks:

```bash
npm run typecheck
npm run build
```

## How it talks to the gateway

The browser only ever calls this app's own origin. `app/gw/[...path]/route.ts`
forwards to the gateway server-side, streams SSE straight through, and refuses
any path outside the documented contract:

| Browser | Gateway |
| --- | --- |
| `GET /gw/health` | `GET /health` |
| `GET /gw/api/v1/scenarios` | `GET /api/v1/scenarios` |
| `POST /gw/api/v1/investigations` | `POST /api/v1/investigations` |
| `GET /gw/api/v1/investigations/{id}/events` | SSE progress stream |
| `GET /gw/api/v1/investigations/{id}/report(.md)` | report JSON / Markdown |
| `POST /gw/api/v1/datasets` | mint an uploaded dataset |
| `PUT /gw/api/v1/datasets/{id}/sources/{provider}/{kind}` | stream one file in |
| `POST /gw/api/v1/datasets/{id}/seal` | freeze it so a run can read it |
| `GET /gw/api/v1/datasets/{id}` | what the dataset contains |
| `DELETE /gw/api/v1/datasets/{id}` | delete it now |
| `GET /gw/api/v1/datasets/templates/{kind}` | an evidence template to fill in |

That keeps the gateway free of a CORS origin allowlist and keeps credentials out
of the browser entirely. The allowlists are **per method**, not one read set and
one write set: a `DELETE` that could reach any path would be worse than no
`DELETE` at all. Upload bodies are forwarded with `duplex: "half"` so an export
streams through this process rather than being buffered in it, and the route
carries its own byte ceiling and content-type allowlist so the proxy is not a way
around the gateway's caps.

## Your own data

`components/data-source.tsx` is the control above the brief. It offers the
multi-cloud demo, the twelve seeded scenarios, and **Your data**, which reveals a
per-provider drop zone: one required cost export and four optional evidence
files. Each file is one `PUT`, and each row reports the detected format, rows read
versus rows stored, the period the file actually covers, the currency, and any
rejected rows by number and reason.

**Use this data** seals the dataset, and the gateway answers with the brief it
derived from the period found in the data, so the demo dates are never silently
applied to somebody's own export. The UI derives no dates and no totals of its
own, exactly as before.

A cost-only dataset is labelled as such before the run starts and again in the
report: the findings it produces are unexplained increases, and the copy says
which data would raise them. `components/marks.tsx` renders three origins, not
two, because an upload is real data that CloudCause measured and did not verify.

`lib/types.ts` mirrors `packages/contracts`. When a Pydantic model changes,
change it there in the same commit — `tests/ui/test_web_types_mirror_the_contract.py`
reads both and fails when they drift.

## The visual system

Tokens live in `app/globals.css` under `@theme`, in OKLCH.

The scene it is designed for: a platform engineer opens this mid-morning in a
daylit office after finance flags a jump on the weekly bill, and has an hour to
read dense evidence and defend a conclusion to somebody else. That is a reading
task in bright light, so the interface is light, print-legible, and built from
hairlines and tabular figures rather than a dark console of glowing cards.

* **House colour is oxblood**, carrying the rail, the rank numerals, and the
  spend delta, because in this product an increase *is* the subject. Savings
  answer in slate-teal, stale knowledge in ochre. Deliberately not the
  navy-and-violet palette every other cost tool ships.
* **Type pairs on a contrast axis**: Newsreader (serif) for the verdict and each
  root cause, Inter for interface text, JetBrains Mono for every figure, ID, and
  date. Money is tabular everywhere via the `.num` class.
* **Panels, not cards.** One flat `.panel` shape, hairline rules, 8px radius, one
  defined shadow. No nested cards, no glass, no gradients.
* **Motion is functional**: streamed trail rows enter once, the running header
  carries a sweeping hairline, confidence ticks transition. All of it collapses
  under `prefers-reduced-motion`.

Fonts are fetched at build time by `next/font` and then self-hosted, so the
running app makes no third-party requests.

## Pages

| Route | What it is |
| --- | --- |
| `/` | Brief, live trail, and the report as it arrives |
| `/history` | Every investigation the gateway still holds |
| `/investigations/{id}` | One stored investigation, server-rendered |
