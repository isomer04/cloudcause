"use client";

/** Shown when a server render could not reach the gateway; polls until it can. */

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { client } from "@/lib/gateway-client";

const POLL_INTERVAL_MS = 2500;

/** After this long, stop blaming a cold start and surface the local-run hint. */
const COLD_START_GRACE_MS = 45_000;

/** The page's own call, not /health: health can pass while the render still fails. */
const PROBES = {
  scenarios: () => client.scenarios(),
  investigations: () => client.investigations(),
} as const;

export type GatewayResource = keyof typeof PROBES;

export function GatewayWaking({
  detail,
  resource,
}: {
  detail: string;
  resource: GatewayResource;
}) {
  const router = useRouter();
  const [attempts, setAttempts] = useState(0);
  const [recovered, setRecovered] = useState(false);
  const [checking, setChecking] = useState(false);
  const cancelled = useRef(false);

  const probe = useCallback(async (): Promise<boolean> => {
    setChecking(true);
    try {
      await PROBES[resource]();
      if (cancelled.current) return true;
      setRecovered(true);
      router.refresh();
      return true;
    } catch {
      return false;
    } finally {
      setChecking(false);
    }
  }, [resource, router]);

  useEffect(() => {
    cancelled.current = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      if (cancelled.current) return;
      const up = await probe();
      if (up || cancelled.current) return;
      setAttempts((count) => count + 1);
      timer = setTimeout(tick, POLL_INTERVAL_MS);
    };

    timer = setTimeout(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled.current = true;
      clearTimeout(timer);
    };
  }, [probe]);

  const waitedMs = attempts * POLL_INTERVAL_MS;
  const takingLong = waitedMs >= COLD_START_GRACE_MS;

  if (recovered) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="mt-5 rounded-lg border border-rule-strong bg-sunken px-4 py-3.5"
      >
        <h2 className="text-sm font-semibold text-ink">The gateway is up</h2>
        <p className="mt-1 text-sm text-ink-soft">Loading the console&hellip;</p>
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="mt-5 rounded-lg border border-brand-edge bg-brand-tint px-4 py-3.5"
    >
      <h2 className="flex items-center gap-2 text-sm font-semibold text-brand">
        <span className="h-1.5 w-1.5 rounded-full bg-caution pulse-dot" />
        {takingLong ? "Still waiting on the gateway" : "Waking the demo up"}
      </h2>

      <p className="mt-1 max-w-[70ch] text-sm text-ink">
        {takingLong
          ? "This is longer than a cold start usually takes."
          : "This demo scales to zero when nobody is using it, so the first visit after a quiet period waits for the gateway to start."}{" "}
        Checking again automatically — you do not need to refresh.
      </p>

      {takingLong ? (
        <p className="mt-2 max-w-[70ch] text-sm text-ink-soft">
          Running CloudCause locally? Start the gateway with{" "}
          <span className="num">uv run cloudcause-api</span>.
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
        <button
          type="button"
          onClick={() => void probe()}
          disabled={checking}
          className="rounded border border-rule-strong px-2.5 py-1 text-xs text-ink transition-colors duration-150 hover:bg-sunken disabled:opacity-60"
        >
          {checking ? "Checking…" : "Check now"}
        </button>
        <span className="num text-[0.6875rem] text-ink-mute">
          {attempts === 0
            ? "first check…"
            : `${attempts} ${attempts === 1 ? "check" : "checks"} · ${Math.round(waitedMs / 1000)}s`}
        </span>
      </div>

      <p className="num mt-2.5 text-[0.6875rem] leading-relaxed text-ink-mute">{detail}</p>
    </div>
  );
}
