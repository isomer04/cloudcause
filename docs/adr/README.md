# Architecture Decision Records

One file per decision that would otherwise be re-litigated in a code review or an
interview. Numbered, immutable once accepted: a reversal is a new ADR that
supersedes the old one rather than an edit to it.

[`../architecture.md`](../architecture.md) says what the system is. These say why a fork in the road
was taken and what was given up.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-streaming-transport.md) | Server-Sent Events for browser progress, stdio for MCP | Accepted |
| [0002](0002-deterministic-arithmetic.md) | Deterministic code owns every number the report publishes | Accepted |
| [0003](0003-framework-per-cloud.md) | One agent framework per cloud, meeting at an HTTP contract | Accepted |
| [0004](0004-native-tools-and-mcp.md) | Native tool calling inside, MCP at the evidence boundary | Accepted |
| [0005](0005-uploads-are-sealed-server-parsed-datasets.md) | An upload is a sealed, server-parsed dataset addressed by id | Accepted |
| [0006](0006-cost-only-data-cannot-name-a-cause.md) | A cost export alone may never name a cause | Accepted |
