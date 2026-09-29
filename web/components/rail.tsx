import Link from "next/link";

import { NavLinks } from "@/components/nav-links";
import { safeHealth } from "@/lib/gateway-server";

/**
 * The rail carries identity, navigation, and the two facts a reader needs
 * before trusting a number: which mode produced it, and that nothing here can
 * change a cloud resource.
 */
export async function Rail() {
  const health = await safeHealth();
  const modeLabel = health?.live_agents_available
    ? "AI or playbooks per run"
    : "playbooks only, no model key";
  const historyBackend =
    typeof health?.history?.["backend"] === "string" ? (health.history["backend"] as string) : null;

  return (
    <div className="bg-brand-deep text-on-brand lg:sticky lg:top-0 lg:z-(--z-rail) lg:flex lg:h-dvh lg:flex-col">
      <div className="flex items-center justify-between gap-4 px-5 py-4 lg:block lg:px-5 lg:py-6">
        <Link href="/" className="group block">
          <span className="block text-[1.125rem] font-semibold leading-none tracking-[-0.02em] text-on-brand">
            CloudCause
          </span>
          <span className="mt-1.5 block text-[0.6875rem] leading-snug text-on-brand-mute">
            Cost spike investigations
          </span>
        </Link>
        <NavLinks />
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-white/12 px-5 pb-3 pt-2.5 text-[0.6875rem] text-on-brand-mute lg:hidden">
        <span>Read-only</span>
        <span aria-hidden>·</span>
        <span className="num">{health ? health.data_mode : "gateway unreachable"}</span>
        {health ? (
          <>
            <span aria-hidden>·</span>
            <span>{modeLabel}</span>
          </>
        ) : null}
      </div>

      <div className="hidden border-t border-white/12 px-5 py-5 lg:mt-auto lg:block">
        <dl className="space-y-2.5 text-[0.6875rem]">
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-on-brand-mute">Gateway</dt>
            <dd className="num text-on-brand">
              {health ? health.status : "unreachable"}
            </dd>
          </div>
          {health ? (
            <>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-on-brand-mute">Data</dt>
                <dd className="num text-on-brand">{health.data_mode}</dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-on-brand-mute">Investigation</dt>
                <dd className="text-right text-on-brand">{modeLabel}</dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-on-brand-mute">History</dt>
                <dd className="num text-on-brand">{historyBackend ?? "memory"}</dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-on-brand-mute">Contract</dt>
                <dd className="num text-on-brand">{health.contract_version}</dd>
              </div>
            </>
          ) : (
            <p className="text-on-brand-mute">
              Starting up, or not running. The console retries on its own.
            </p>
          )}
        </dl>

        <p className="mt-5 border-t border-white/12 pt-4 text-[0.6875rem] leading-relaxed text-on-brand-mute">
          Read-only by construction. No tool in CloudCause can delete, stop, scale, or modify a
          cloud resource.
        </p>
      </div>
    </div>
  );
}
