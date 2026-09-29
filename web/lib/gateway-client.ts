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
import { API_PREFIX, BROWSER_BASE, GatewayError, readGatewayError } from "./gateway-shared";

async function browserGet<T>(path: string): Promise<T> {
  const response = await fetch(`${BROWSER_BASE}${path}`, {
    headers: { accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) throw new GatewayError(await readGatewayError(response), response.status);
  return (await response.json()) as T;
}

export { GatewayError };

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
    if (!response.ok) {
      if (response.status === 429) {
        const body = (await response.json().catch(() => ({}))) as {
          detail?: string;
          retry_after_seconds?: number;
        };
        throw new GatewayError(
          typeof body.detail === "string" ? body.detail : "Too many live investigations were started.",
          429,
          typeof body.retry_after_seconds === "number" ? body.retry_after_seconds : undefined,
        );
      }
      throw new GatewayError(await readGatewayError(response), response.status);
    }
    return (await response.json()) as InvestigationCreated;
  },
  eventsUrl: (id: string) =>
    `${BROWSER_BASE}${API_PREFIX}/investigations/${encodeURIComponent(id)}/events`,
  markdownUrl: (id: string) =>
    `${BROWSER_BASE}${API_PREFIX}/investigations/${encodeURIComponent(id)}/report.md`,
  pdfUrl: (id: string) =>
    `${BROWSER_BASE}${API_PREFIX}/investigations/${encodeURIComponent(id)}/report.pdf`,
  jsonUrl: (id: string) =>
    `${BROWSER_BASE}${API_PREFIX}/investigations/${encodeURIComponent(id)}/report`,

  /** Mint an empty dataset. The response carries every limit the client must respect. */
  async createDataset(): Promise<DatasetCreated> {
    const response = await fetch(`${BROWSER_BASE}${API_PREFIX}/datasets`, {
      method: "POST",
      headers: { accept: "application/json" },
    });
    if (!response.ok) throw new GatewayError(await readGatewayError(response), response.status);
    return (await response.json()) as DatasetCreated;
  },

  /** Send one source as a raw body without retaining browser-local metadata. */
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
    if (!response.ok) throw new GatewayError(await readGatewayError(response), response.status);
    return (await response.json()) as DatasetIngestReport;
  },

  async sealDataset(datasetId: string): Promise<DatasetSummary> {
    const response = await fetch(
      `${BROWSER_BASE}${API_PREFIX}/datasets/${encodeURIComponent(datasetId)}/seal`,
      { method: "POST", headers: { accept: "application/json" } },
    );
    if (!response.ok) throw new GatewayError(await readGatewayError(response), response.status);
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
      throw new GatewayError(await readGatewayError(response), response.status);
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
