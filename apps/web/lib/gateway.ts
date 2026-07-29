/**
 * The only place that knows how to reach CloudCause.
 *
 * Server components call the gateway directly. Browser code calls the same
 * paths under /gw, which the proxy route forwards. No investigation logic here:
 * this module moves JSON.
 */

import type {
  DatasetCreated,
  DatasetIngestReport,
  DatasetSourceKind,
  DatasetSummary,
  GatewayHealth,
  InvestigationCreated,
  InvestigationReport,
  InvestigationRequest,
  InvestigationState,
  ProgressEvent,
  Provider,
  ScenarioSummary,
} from "./types";

export const GATEWAY_URL = (
  process.env.CLOUDCAUSE_API_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

export const API_PREFIX = "/api/v1";
export const BROWSER_BASE = "/gw";

export class GatewayError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "GatewayError";
    this.status = status;
  }
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string; error?: string };
    return body.detail ?? body.error ?? response.statusText;
  } catch {
    return response.statusText || `HTTP ${response.status}`;
  }
}

/* ------------------------------------------------------------------ server */

async function serverGet<T>(path: string): Promise<T> {
  const response = await fetch(`${GATEWAY_URL}${path}`, {
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  if (!response.ok) throw new GatewayError(await readError(response), response.status);
  return (await response.json()) as T;
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

/* ----------------------------------------------------------------- browser */

async function browserGet<T>(path: string): Promise<T> {
  const response = await fetch(`${BROWSER_BASE}${path}`, {
    headers: { accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) throw new GatewayError(await readError(response), response.status);
  return (await response.json()) as T;
}

export const client = {
  health: () => browserGet<GatewayHealth>("/health"),
  scenarios: () => browserGet<ScenarioSummary[]>(`${API_PREFIX}/scenarios`),
  investigations: () => browserGet<InvestigationState[]>(`${API_PREFIX}/investigations`),
  state: (id: string) =>
    browserGet<InvestigationState>(`${API_PREFIX}/investigations/${encodeURIComponent(id)}`),
  progress: (id: string) =>
    browserGet<ProgressEvent[]>(`${API_PREFIX}/investigations/${encodeURIComponent(id)}/progress`),
  report: (id: string) =>
    browserGet<InvestigationReport>(`${API_PREFIX}/investigations/${encodeURIComponent(id)}/report`),
  async start(request: InvestigationRequest): Promise<InvestigationCreated> {
    const response = await fetch(`${BROWSER_BASE}${API_PREFIX}/investigations`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(request),
    });
    if (!response.ok) throw new GatewayError(await readError(response), response.status);
    return (await response.json()) as InvestigationCreated;
  },
  eventsUrl: (id: string) =>
    `${BROWSER_BASE}${API_PREFIX}/investigations/${encodeURIComponent(id)}/events`,
  markdownUrl: (id: string) =>
    `${BROWSER_BASE}${API_PREFIX}/investigations/${encodeURIComponent(id)}/report.md`,
  jsonUrl: (id: string) =>
    `${BROWSER_BASE}${API_PREFIX}/investigations/${encodeURIComponent(id)}/report`,

  /* ---------------------------------------------------------- your own data */

  /** Mint an empty dataset. The response carries every limit the client must respect. */
  async createDataset(): Promise<DatasetCreated> {
    const response = await fetch(`${BROWSER_BASE}${API_PREFIX}/datasets`, {
      method: "POST",
      headers: { accept: "application/json" },
    });
    if (!response.ok) throw new GatewayError(await readError(response), response.status);
    return (await response.json()) as DatasetCreated;
  },

  /**
   * Send one file as a raw body. There is no multipart and no filename: the
   * source is addressed by {provider}/{kind}, so nothing about the local file
   * except its bytes ever leaves the browser.
   */
  async putSource(
    datasetId: string,
    provider: Provider,
    kind: DatasetSourceKind,
    file: File,
  ): Promise<DatasetIngestReport> {
    const response = await fetch(
      `${BROWSER_BASE}${API_PREFIX}/datasets/${encodeURIComponent(datasetId)}/sources/${provider}/${kind}`,
      {
        method: "PUT",
        headers: { "content-type": contentTypeFor(file), accept: "application/json" },
        body: file,
      },
    );
    if (!response.ok) throw new GatewayError(await readError(response), response.status);
    return (await response.json()) as DatasetIngestReport;
  },

  async sealDataset(datasetId: string): Promise<DatasetSummary> {
    const response = await fetch(
      `${BROWSER_BASE}${API_PREFIX}/datasets/${encodeURIComponent(datasetId)}/seal`,
      { method: "POST", headers: { accept: "application/json" } },
    );
    if (!response.ok) throw new GatewayError(await readError(response), response.status);
    return (await response.json()) as DatasetSummary;
  },

  dataset: (datasetId: string) =>
    browserGet<DatasetSummary>(`${API_PREFIX}/datasets/${encodeURIComponent(datasetId)}`),

  /** Honoured immediately by the gateway; the dataset is gone when this returns. */
  async deleteDataset(datasetId: string): Promise<void> {
    const response = await fetch(
      `${BROWSER_BASE}${API_PREFIX}/datasets/${encodeURIComponent(datasetId)}`,
      { method: "DELETE" },
    );
    if (!response.ok && response.status !== 404) {
      throw new GatewayError(await readError(response), response.status);
    }
  },

  templateUrl: (kind: DatasetSourceKind) =>
    `${BROWSER_BASE}${API_PREFIX}/datasets/templates/${kind}`,
};

/**
 * The gateway allowlists three media types. A browser gives `File.type` as ""
 * for a `.csv` on some platforms, so the extension decides rather than the OS.
 */
export function contentTypeFor(file: File): string {
  const name = file.name.toLowerCase();
  if (name.endsWith(".gz") || name.endsWith(".gzip")) return "application/gzip";
  if (name.endsWith(".csv")) return "text/csv";
  if (name.endsWith(".json")) return "application/json";
  if (file.type === "text/csv" || file.type === "application/gzip") return file.type;
  return "application/json";
}
