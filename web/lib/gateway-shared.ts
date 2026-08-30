/** Values and errors safe to share between server and browser gateway callers. */

export const API_PREFIX = "/api/v1";
export const BROWSER_BASE = "/gw";

export class GatewayError extends Error {
  readonly status: number;
  /** Seconds to wait before resubmitting; only set for a 429 response. */
  readonly retryAfterSeconds?: number;

  constructor(message: string, status: number, retryAfterSeconds?: number) {
    super(message);
    this.name = "GatewayError";
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

/** FastAPI answers a 422 with `detail` as a list of validation records. */
type GatewayErrorBody = {
  detail?: string | { msg?: unknown }[];
  error?: string;
  retry_after_seconds?: number;
};

export async function readGatewayError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as GatewayErrorBody;
    const detail = body.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      // A validation record carries `input`, which for an upload is the rejected
      // row itself. Take the human-readable `msg` only; never surface the record.
      const described = detail.find((entry) => typeof entry?.msg === "string");
      return typeof described?.msg === "string"
        ? described.msg
        : "the gateway rejected the request as invalid";
    }
    return body.error ?? response.statusText;
  } catch {
    return response.statusText || `HTTP ${response.status}`;
  }
}
