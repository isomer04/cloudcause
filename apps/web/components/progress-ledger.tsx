import { ProviderMark } from "@/components/marks";
import { clockTime } from "@/lib/format";
import type { InvestigationStatus, ProgressEvent } from "@/lib/types";

const STATUS_COPY: Record<InvestigationStatus, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Complete",
  failed: "Failed",
};

/**
 * The investigation trail, in order, with the stage that produced each line.
 * It stays on screen after the report arrives: the reader can see how the
 * conclusion was reached, not just what it was.
 */
export function ProgressLedger({
  investigationId,
  status,
  events,
  error,
}: {
  investigationId: string;
  status: InvestigationStatus;
  events: ProgressEvent[];
  error?: string | null;
}) {
  const running = status === "queued" || status === "running";
  const shown = events.slice(-24);

  return (
    <section className="panel overflow-hidden" aria-label="Investigation trail">
      <header
        className={`relative flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-rule px-4 py-2.5 ${
          running ? "sweep" : ""
        }`}
      >
        <div className="flex items-baseline gap-2.5">
          <h2 className="text-sm font-medium text-ink">Trail</h2>
          <span className="num text-xs text-ink-mute">{investigationId}</span>
        </div>
        <span className="flex items-center gap-1.5 text-xs">
          <span
            aria-hidden
            className={`h-1.5 w-1.5 rounded-full ${
              status === "failed"
                ? "bg-brand"
                : status === "completed"
                  ? "bg-verified"
                  : "bg-caution pulse-dot"
            }`}
          />
          <span className={status === "failed" ? "text-brand" : "text-ink-soft"}>
            {STATUS_COPY[status]}
          </span>
        </span>
      </header>

      <ol className="divide-y divide-rule">
        {shown.length === 0 ? (
          <li className="px-4 py-3 text-sm text-ink-mute">{"Waiting for the first stage\u2026"}</li>
        ) : null}
        {shown.map((event) => (
          <li
            key={event.sequence}
            className="enter-row grid grid-cols-[4.5rem_5.5rem_minmax(0,1fr)] items-baseline gap-x-3 px-4 py-2 text-sm sm:grid-cols-[5rem_6rem_minmax(0,1fr)]"
          >
            <span className="num text-xs text-ink-mute">{clockTime(event.at)}</span>
            <span
              className={`num text-[0.6875rem] uppercase tracking-[0.06em] ${
                event.status === "failed" ? "text-brand" : "text-ink-soft"
              }`}
            >
              {event.stage}
            </span>
            <span className="min-w-0 text-ink">
              {event.provider ? (
                <>
                  <ProviderMark provider={event.provider} />{" "}
                </>
              ) : null}
              {event.message}
            </span>
          </li>
        ))}
      </ol>

      {error ? (
        <p className="border-t border-brand-edge bg-brand-tint px-4 py-3 text-sm text-brand">
          {error}
        </p>
      ) : null}
    </section>
  );
}
