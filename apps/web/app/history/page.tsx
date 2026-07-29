import Link from "next/link";

import { ProviderMark, Tag } from "@/components/marks";
import { delta, percent, range, stamp } from "@/lib/format";
import { server } from "@/lib/gateway";
import type { InvestigationState } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function HistoryPage() {
  let investigations: InvestigationState[] = [];
  let gatewayError: string | null = null;
  try {
    investigations = await server.investigations();
  } catch (error) {
    gatewayError = (error as Error).message;
  }

  const ordered = [...investigations].reverse();

  return (
    <div className="mx-auto w-full max-w-6xl px-5 py-7 sm:px-8 lg:py-10">
      <header className="max-w-[68ch]">
        <h1 className="font-display text-[1.75rem] leading-tight text-ink">History</h1>
        <p className="mt-2 text-sm leading-relaxed text-ink-soft">
          Every investigation the gateway still holds. With a SQL history backend configured these
          survive a restart; on the default memory backend they do not.
        </p>
      </header>

      {gatewayError ? (
        <p className="mt-6 rounded-lg border border-brand-edge bg-brand-tint px-4 py-3.5 text-sm text-ink">
          The gateway is not answering: {gatewayError}
        </p>
      ) : ordered.length === 0 ? (
        <p className="mt-6 panel px-5 py-6 text-sm text-ink-soft">
          Nothing yet.{" "}
          <Link href="/" className="underline decoration-rule-strong underline-offset-2 hover:text-brand">
            Open an investigation
          </Link>{" "}
          and it will appear here.
        </p>
      ) : (
        <div className="panel mt-6 divide-y divide-rule">
          {ordered.map((state) => (
            <Link
              key={state.investigation_id}
              href={`/investigations/${state.investigation_id}`}
              className="block px-4 py-4 transition-colors duration-150 hover:bg-sunken sm:px-6"
            >
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                <span className="num text-xs text-ink-mute">{state.investigation_id}</span>
                <StatusTag status={state.status} />
                {state.request.providers.map((provider) => (
                  <ProviderMark key={provider} provider={provider} />
                ))}
                {state.report ? (
                  <span className="num ml-auto text-sm text-brand">
                    {delta(state.report.total_absolute_change, state.report.currency)}
                    <span className="ml-2 text-ink-mute">
                      {percent(state.report.total_percent_change)}
                    </span>
                  </span>
                ) : null}
              </div>
              <p className="mt-1.5 max-w-[70ch] font-display text-[1.0625rem] leading-snug text-ink">
                {state.question}
              </p>
              <p className="mt-1 text-xs text-ink-mute">
                {range(state.request.start_date, state.request.end_date)} · started{" "}
                {stamp(state.created_at)}
                {state.report
                  ? ` · ${state.report.findings.length} ${
                      state.report.findings.length === 1 ? "finding" : "findings"
                    }`
                  : state.message
                    ? ` · ${state.message}`
                    : ""}
              </p>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function StatusTag({ status }: { status: InvestigationState["status"] }) {
  if (status === "completed") return <Tag tone="savings">complete</Tag>;
  if (status === "failed") return <Tag tone="brand">failed</Tag>;
  return <Tag tone="caution">{status}</Tag>;
}
