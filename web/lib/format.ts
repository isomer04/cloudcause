/**
 * Presentation only. Every number here was computed by the gateway's
 * deterministic analytics; this module chooses how to print it.
 */

import type { Provider, Risk, WorkerStatus } from "./types";

export function money(value: number, currency = "USD"): string {
  const formatter = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  });
  return formatter.format(value);
}

/** Signed money, for a change rather than a level. */
export function delta(value: number, currency = "USD"): string {
  const sign = value > 0 ? "+" : value < 0 ? "\u2212" : "";
  return `${sign}${money(Math.abs(value), currency)}`;
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
  const sign = value > 0 ? "+" : value < 0 ? "\u2212" : "";
  return `${sign}${Math.abs(value).toFixed(digits)}%`;
}

export function confidencePercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function day(value: string | null | undefined): string {
  if (!value) return "unknown";
  const parsed = new Date(value.length <= 10 ? `${value}T00:00:00Z` : value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString("en-CA", { timeZone: "UTC" });
}

export function stamp(value: string | null | undefined): string {
  if (!value) return "unknown";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return `${parsed.toLocaleDateString("en-CA", { timeZone: "UTC" })} ${parsed.toLocaleTimeString(
    "en-GB",
    { timeZone: "UTC", hour: "2-digit", minute: "2-digit" },
  )}Z`;
}

/**
 * Wall-clock duration of a step. Deterministic playbooks finish a provider in
 * tens of milliseconds, and printing that as "0.0s" advertises that no work
 * happened. Report the unit the measurement actually supports.
 */
export function duration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds < 1) return `${Math.max(Math.round(seconds * 1000), 1)} ms`;
  if (seconds < 10) return `${seconds.toFixed(2)}s`;
  return `${seconds.toFixed(1)}s`;
}

/**
 * Offset of an event from the start of the run. A twelve-step ledger that
 * completes inside one second shows the same wall-clock second on every row;
 * the offset is the part that carries information.
 */
export function offset(value: string, origin: string): string {
  const at = new Date(value).getTime();
  const from = new Date(origin).getTime();
  if (Number.isNaN(at) || Number.isNaN(from)) return "";
  const ms = Math.max(at - from, 0);
  if (ms < 1000) return `+${ms} ms`;
  return `+${(ms / 1000).toFixed(2)}s`;
}

export function clockTime(value: string | null | undefined): string {
  if (!value) return "--:--:--";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "--:--:--";
  return parsed.toLocaleTimeString("en-GB", {
    timeZone: "UTC",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function range(start: string, end: string): string {
  return `${day(start)} \u2192 ${day(end)}`;
}

/** "nat_gateway_misroute" reads better as "NAT gateway misroute" in a heading. */
export function humanizeCategory(value: string): string {
  const words = value.replace(/[_-]+/g, " ").trim();
  const capitalized = words.charAt(0).toUpperCase() + words.slice(1);
  return capitalized
    .replace(/\bnat\b/gi, "NAT")
    .replace(/\bapi\b/gi, "API")
    .replace(/\bec2\b/gi, "EC2")
    .replace(/\bs3\b/gi, "S3")
    .replace(/\bvpc\b/gi, "VPC")
    .replace(/\biam\b/gi, "IAM")
    .replace(/\bsku\b/gi, "SKU")
    .replace(/\bgpu\b/gi, "GPU");
}

export const PROVIDER_LABEL: Record<Provider, string> = {
  aws: "AWS",
  azure: "Azure",
  gcp: "Google Cloud",
};

export const PROVIDER_SHORT: Record<Provider, string> = {
  aws: "AWS",
  azure: "AZ",
  gcp: "GCP",
};

export const RISK_LABEL: Record<Risk, string> = {
  low: "Low risk",
  medium: "Medium risk",
  high: "High risk",
};

export const WORKER_STATUS_LABEL: Record<WorkerStatus, string> = {
  ok: "Complete",
  partial: "Partial",
  failed: "Failed",
  skipped: "No data",
};

export function truncate(value: string, limit: number): string {
  return value.length <= limit ? value : `${value.slice(0, limit - 1)}\u2026`;
}
