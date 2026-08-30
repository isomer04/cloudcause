import Link from "next/link";

import { GatewayWaking } from "@/components/gateway-waking";
import { ProviderMark, Tag } from "@/components/marks";
import { delta, percent, range, stamp } from "@/lib/format";
import { server } from "@/lib/gateway-server";
import type { InvestigationState } from "@/lib/types";

export const dynamic = "force-dynamic";

type HistoryGroup = {
  latest: InvestigationState;
  runs: number;
  oldestAt: string;
};

/**
 * Re-running the same question over the same period is how anyone tests this
 * product, and seven identical rows make it look like the answer is canned
 * rather than recomputed. Collapse repeats onto the newest run and say how many
 * there were; the run count is the honest fact, and each run is still reachable
 * from its own report.
 */
function groupRepeatRuns(investigations: InvestigationState[]): HistoryGroup[] {
  const groups = new Map<string, HistoryGroup>();
  for (const state of investigations) {
    // JSON.stringify rather than a delimiter join: a question containing the
    // separator would otherwise let two different requests collide on one key.
    const key = JSON.stringify([
      state.question,
      state.request.start_date,
      state.request.end_date,
      state.request.comparison_start_date,
      state.request.comparison_end_date,
      [...state.request.providers].sort(),
      state.request.dataset_id ?? "",
      state.request.scenario_id,
      state.status,
    ]);
    const existing = groups.get(key);
    if (!existing) {
      groups.set(key, { latest: state, runs: 1, oldestAt: state.created_at });
      continue;
    }
    existing.runs += 1;
    if (state.created_at > existing.latest.created_at) existing.latest = state;
    if (state.created_at < existing.oldestAt) existing.oldestAt = state.created_at;
  }
  return [...groups.values()];
}

export default async function HistoryPage() {
  let investigations: InvestigationState[] = [];
  let gatewayError: string | null = null;
  try {
    investigations = await server.investigations();
  } catch (error) {
    gatewayError = (error as Error).message;
  }

  const groups = groupRepeatRuns(investigations);
  const collapsed = investigations.length - groups.length;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-5 py-7 sm:px-8 lg:py-10">
      <header className="max-w-[68ch]">
        <h1 className="font-display text-[1.625rem] leading-tight text-ink">History</h1>
        <p className="mt-2 text-sm leading-relaxed text-ink-soft">
          Every investigation the gateway still holds. With a SQL history backend configured these
          survive a restart; on the default memory backend they do not.
          {collapsed > 0
            ? ` Repeat runs of the same question and period are collapsed onto the newest run, ${collapsed} of them here.`
            : ""}
        </p>
      </header>

      {gatewayError ? (
        <GatewayWaking detail={gatewayError} resource="investigations" />
      ) : groups.length === 0 ? (
        <p className="panel px-5 py-6 text-sm text-ink-soft">
          Nothing yet.{" "}
          <Link href="/" className="underline decoration-rule-strong underline-offset-2 hover:text-brand">
            Open an investigation
          </Link>{" "}
          and it will appear here.
        </p>
      ) : (
        <div className="panel divide-y divide-rule">
          {groups.map(({ latest: state, runs }) => (
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
                {runs > 1 ? <Tag>{runs} runs</Tag> : null}
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
                {range(state.request.start_date, state.request.end_date)} ·{" "}
                {runs > 1 ? "latest run" : "started"} {stamp(state.created_at)}
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
