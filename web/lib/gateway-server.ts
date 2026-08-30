import "server-only";

import type {
  GatewayHealth,
  InvestigationReport,
  InvestigationState,
  ProgressEvent,
  ScenarioSummary,
} from "./types";
import { API_PREFIX, GatewayError, readGatewayError } from "./gateway-shared";

/** The private upstream gateway address; never import this module from a client graph. */
export const GATEWAY_URL = (
  process.env.CLOUDCAUSE_API_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

/**
 * How long a Server Component will wait on the gateway before giving up.
 *
 * These reads all block a render, so an unreachable gateway that accepts the
 * connection and then stalls would hang the page rather than fall through to
 * the offline state. Bounded here because `fetch` has no deadline of its own.
 */
const REQUEST_TIMEOUT_MS = (() => {
  const configured = Number(process.env.CLOUDCAUSE_WEB_GATEWAY_TIMEOUT_SECONDS);
  return (Number.isFinite(configured) && configured > 0 ? configured : 10) * 1000;
})();

async function serverGet<T>(path: string): Promise<T> {
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${GATEWAY_URL}${path}`, {
      cache: "no-store",
      headers: { accept: "application/json" },
      signal: controller.signal,
    });
    if (!response.ok) throw new GatewayError(await readGatewayError(response), response.status);
    return (await response.json()) as T;
  } finally {
    // Cleared only once the body is read, so the deadline covers the whole read.
    clearTimeout(deadline);
  }
}

export const server = {
  health: () => serverGet<GatewayHealth>("/health"),
  scenarios: () => serverGet<ScenarioSummary[]>(`${API_PREFIX}/scenarios`),
  investigations: () => serverGet<InvestigationState[]>(`${API_PREFIX}/investigations`),
  investigation: (id: string) =>
    serverGet<InvestigationState>(`${API_PREFIX}/investigations/${encodeURIComponent(id)}`),
  progress: (id: string) =>
    serverGet<ProgressEvent[]>(`${API_PREFIX}/investigations/${encodeURIComponent(id)}/progress`),
  report: (id: string) =>
    serverGet<InvestigationReport>(`${API_PREFIX}/investigations/${encodeURIComponent(id)}/report`),
};

/** Never throws: the shell renders an offline state instead of a stack trace. */
export async function safeHealth(): Promise<GatewayHealth | null> {
  try {
    return await server.health();
  } catch {
    return null;
  }
}
