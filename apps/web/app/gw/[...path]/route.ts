/**
 * Server-side proxy to the CloudCause gateway.
 *
 * Why a proxy instead of calling the gateway from the browser: the gateway
 * grants no cross-origin access, and it should not have to. The browser talks
 * to its own origin, this route forwards to the gateway inside the trust
 * boundary, and SSE is piped straight through unbuffered.
 *
 * The allowlists keep this from becoming an open forwarder. They are per method,
 * not one read set and one write set, because ingest adds PUT and DELETE and a
 * DELETE that could reach any path would be worse than no DELETE at all.
 *
 * Upload bodies are streamed with `duplex: "half"` rather than buffered into this
 * Node process, and this route enforces its own byte ceiling so the proxy is not
 * a way around the gateway's cap.
 */

import { GATEWAY_URL } from "@/lib/gateway";

export const dynamic = "force-dynamic";

const INVESTIGATION = String.raw`[A-Za-z0-9_-]+`;
/** `secrets.token_urlsafe`, so base64url characters only. */
const DATASET = String.raw`[A-Za-z0-9_-]+`;
const PROVIDER = String.raw`aws|azure|gcp`;
const SOURCE_KIND = String.raw`cost|metrics|audit|inventory|recommendations`;
/** Cost exports are provider-native, so the gateway offers no template for them. */
const TEMPLATE_KIND = String.raw`metrics|audit|inventory|recommendations`;

const GET_PATTERNS: RegExp[] = [
  /^health$/,
  /^api\/v1\/scenarios$/,
  /^api\/v1\/investigations$/,
  new RegExp(String.raw`^api/v1/investigations/${INVESTIGATION}$`),
  new RegExp(
    String.raw`^api/v1/investigations/${INVESTIGATION}/(events|progress|report|report\.md|wait)$`,
  ),
  new RegExp(String.raw`^api/v1/datasets/templates/(${TEMPLATE_KIND})$`),
  new RegExp(String.raw`^api/v1/datasets/${DATASET}$`),
];

const POST_PATTERNS: RegExp[] = [
  /^api\/v1\/investigations$/,
  /^api\/v1\/datasets$/,
  new RegExp(String.raw`^api/v1/datasets/${DATASET}/seal$`),
];

const PUT_PATTERNS: RegExp[] = [
  new RegExp(String.raw`^api/v1/datasets/${DATASET}/sources/(${PROVIDER})/(${SOURCE_KIND})$`),
];

const DELETE_PATTERNS: RegExp[] = [new RegExp(String.raw`^api/v1/datasets/${DATASET}$`)];

const PATTERNS: Record<string, RegExp[]> = {
  GET: GET_PATTERNS,
  POST: POST_PATTERNS,
  PUT: PUT_PATTERNS,
  DELETE: DELETE_PATTERNS,
};

/** Mirrors CLOUDCAUSE_UPLOAD_MAX_BYTES. The gateway enforces the real limit. */
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

const ALLOWED_CONTENT_TYPES = ["application/json", "text/csv", "application/gzip"];

const FORWARDED_RESPONSE_HEADERS = ["content-type", "cache-control", "content-disposition"];

function deny(detail: string, status = 403, error = "path_not_allowed"): Response {
  return Response.json({ error, detail }, { status });
}

function contentTypeIsAllowed(value: string | null): boolean {
  const media = (value ?? "").split(";")[0]?.trim().toLowerCase() ?? "";
  return ALLOWED_CONTENT_TYPES.includes(media) || media === "application/x-gzip";
}

async function proxy(request: Request, segments: string[]): Promise<Response> {
  const method = request.method.toUpperCase();
  const patterns = PATTERNS[method];
  if (!patterns) return deny(`${method} is not forwarded`, 405, "method_not_allowed");

  const decoded = segments.join("/");
  if (!patterns.some((pattern) => pattern.test(decoded))) {
    return deny(`${method} /${decoded} is not part of the gateway contract`);
  }

  const path = segments.map((segment) => encodeURIComponent(segment)).join("/");
  const target = `${GATEWAY_URL}/${path}${new URL(request.url).search}`;

  const headers = new Headers();
  const accept = request.headers.get("accept");
  if (accept) headers.set("accept", accept);

  const sendsBody = method === "POST" || method === "PUT";
  let body: BodyInit | undefined;
  if (sendsBody) {
    if (method === "PUT") {
      const contentType = request.headers.get("content-type");
      if (!contentTypeIsAllowed(contentType)) {
        return deny(
          `content-type ${contentType ?? "missing"} is not accepted for an upload; use ` +
            ALLOWED_CONTENT_TYPES.join(", "),
          415,
          "unsupported_content_type",
        );
      }
      const rawLength = request.headers.get("content-length");
      const declared = rawLength === null ? NaN : Number(rawLength);
      if (!Number.isInteger(declared) || declared < 0) {
        // Neither missing nor unparseable may fall through as zero: the gateway
        // still enforces the real limit while streaming, but a proxy that skips
        // its own check on a malformed header is a check that does nothing.
        return deny(
          `an upload must declare a valid content-length; got ${rawLength ?? "no header"}`,
          411,
          "length_required",
        );
      }
      if (declared > MAX_UPLOAD_BYTES) {
        return deny(
          `the upload declares ${declared} bytes, over the ${MAX_UPLOAD_BYTES} byte limit`,
          413,
          "upload_too_large",
        );
      }
      // The client's own content-type, not a hardcoded one: a CSV must not be
      // announced as JSON, or content sniffing on the gateway would refuse it.
      headers.set("content-type", contentType as string);
      body = request.body ?? undefined;
    } else {
      headers.set("content-type", "application/json");
      body = await request.text();
    }
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method,
      headers,
      body,
      cache: "no-store",
      signal: request.signal,
      // Required when body is a stream: this process forwards bytes without
      // buffering the whole export in memory.
      ...(body instanceof ReadableStream ? { duplex: "half" } : {}),
    } as RequestInit);
  } catch (error) {
    return Response.json(
      {
        error: "gateway_unreachable",
        detail: `${GATEWAY_URL} did not answer: ${(error as Error).message}`,
      },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  if (decoded.endsWith("/events")) {
    responseHeaders.set("cache-control", "no-cache, no-transform");
    responseHeaders.set("x-accel-buffering", "no");
    responseHeaders.set("connection", "keep-alive");
  }

  if (upstream.status === 204) {
    return new Response(null, { status: 204, headers: responseHeaders });
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

type RouteContext = { params: Promise<{ path: string[] }> };

async function handle(request: Request, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  return proxy(request, path ?? []);
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const DELETE = handle;
