# ADR 0001: Server-Sent Events for browser progress, stdio for MCP

* Status: Accepted
* Date: 2026-07-28
* Deciders: Rashed Khan
* Scope: `apps/api` gateway streaming endpoint, `packages/mcp_servers` transport,
  `apps/web` client

## Context

An investigation is long-running: the gateway accepts a request, the ADK
coordinator delegates to the AWS and Azure specialists, and a report appears
seconds to minutes later. The UI has to show progress while that happens.

Two unrelated transport questions get confused because both involve the term SSE:

1. How the gateway pushes progress to a browser.
2. Which MCP transport the agent services use to reach the operational-data and
   billing-knowledge servers.

The MCP specification deprecated its `HTTP+SSE` transport in protocol version
2025-03-26 and replaced it with Streamable HTTP, so "avoid SSE" is correct advice
in an MCP context and wrong if applied to question 1.

## Decision

**Browser progress uses Server-Sent Events.** `GET /api/investigations/{id}/events`
returns a `StreamingResponse` with `media_type="text/event-stream"`, emits one
`data:` frame per `ProgressEvent`, and ends with an `event: close` frame.
`Cache-Control: no-cache` and `X-Accel-Buffering: no` are set so a reverse proxy
does not buffer the stream.

**MCP uses stdio.** The Strands, MAF, and ADK services launch both MCP servers as
local subprocesses through the parameters in
`packages/mcp_servers/src/cloudcause_mcp/client.py`. The deprecated `HTTP+SSE`
transport is not used, and neither is Streamable HTTP, because the servers are
local processes rather than remote services.

**Polling stays as a first-class fallback.** `GET .../progress` returns the full
event log and `GET .../wait` blocks until a terminal state, so a client that
cannot hold an open stream is still fully functional.

## Rationale

The traffic is one-way. The client fires one POST and then only listens; it never
needs to send anything mid-investigation. SSE is the standard built for exactly
that shape, and `EventSource` supplies event framing, automatic reconnect, and
`Last-Event-ID` resumption without a client library.

SSE is also the mechanism the surrounding ecosystem settled on for token and
progress streaming, including the OpenAI and Anthropic APIs and MCP's own
Streamable HTTP transport, which streams server messages over SSE internally.
MCP retired a specific two-endpoint arrangement, not the mechanism.

Running over HTTPS in deployment means HTTP/2, which multiplexes streams over one
connection and removes the six-connections-per-origin limit that was the usual
objection to SSE.

## Alternatives considered

**WebSockets.** Full-duplex, binary frames. Rejected: the duplex channel is a
capability the UI never calls, and it costs a protocol upgrade that some proxies
mishandle, plus hand-written reconnect and heartbeat logic. Revisit if the UI ever
needs to steer a running investigation, for example cancelling a single specialist
mid-run.

**`fetch` + `ReadableStream`.** Same wire format, manual framing, and it can carry
a POST body and custom headers, which `EventSource` cannot. Rejected for now
because the endpoint is a GET keyed by investigation id. This is the migration
path the moment the gateway requires auth headers, and it does not change the
server.

**Polling only.** Simpler, but either lags behind the investigation or hammers the
gateway. Kept as a fallback rather than the primary path.

**gRPC server streaming.** Needs gRPC-web plus a translating proxy to reach a
browser. Disproportionate machinery for a progress feed.

**MCP over Streamable HTTP.** Correct choice for a remote MCP service. Rejected
because these servers are local subprocesses, where stdio removes network setup,
ports, and an auth surface entirely.

## Consequences

Accepted:

* SSE is text-only and unidirectional. Neither constrains a progress feed.
* Live jobs sit in an in-memory queue per gateway process, so an SSE request has
  to reach the process holding the job. A single gateway process is fine today.
  Horizontal scaling requires sticky sessions, Redis pub/sub for event fan-out, or
  letting the client fall back to `/progress`, which reads the shared history
  backend and works across replicas.
* Serverless hosts cut long-lived connections at hard timeouts. The Next.js client
  must connect to the FastAPI gateway directly rather than proxying the stream
  through a route handler on a serverless platform, otherwise long investigations
  are truncated.
* A reverse proxy in front of the gateway needs `proxy_buffering off` on this
  location, matching the `X-Accel-Buffering` header the endpoint already sends.

Verified by `tests/gateway/test_gateway_api.py`, which exercises the streaming
endpoint alongside the polling endpoints.

## References

* [MCP transports, Streamable HTTP replaces HTTP+SSE](https://modelcontextprotocol.io/specification/draft/basic/transports/streamable-http)
* [MCP 2025-06-18 transports: Streamable HTTP may stream server messages over SSE](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
* [MCP TypeScript SDK marks HTTP+SSE deprecated](https://ts.sdk.modelcontextprotocol.io/server.html)
* [Cloudflare Agents: SSE deprecated in favour of Streamable HTTP](https://developers.cloudflare.com/agents/model-context-protocol/protocol/transport/)
* [Serverless timeouts and proxy buffering break long-lived streams](https://www.w3tweaks.com/javascript/javascript-polling-websockets-sse/)
* [FastAPI StreamingResponse consumed by a Next.js client](https://damianhodgkiss.hashnode.dev/streaming-llm-responses-from-fastapi-to-next-js-with-the-openai-and-anthropic-apis)

External source content is paraphrased for licensing compliance.
