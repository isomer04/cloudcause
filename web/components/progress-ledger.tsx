import { ProviderMark } from "@/components/marks";
import { clockTime, offset } from "@/lib/format";
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
  // Offsets are measured from the first event of the whole run, not of the
  // window, so trimming to the last 24 rows cannot restart the clock.
  const origin = events[0]?.at ?? shown[0]?.at ?? null;

  return (
    <section id="trail" className="overflow-hidden border-t border-rule pt-5" aria-label="Investigation trail">
      <header
        className={`relative border-b border-rule px-1 pb-3 ${
          running ? "sweep" : ""
        }`}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-xs font-semibold text-ink">Investigation timeline</h2>
            <p className="text-[0.6875rem] text-ink-mute">elapsed from the first stage</p>
          </div>
          <span className="flex shrink-0 items-center gap-1.5 text-xs">
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
        </div>
        <span className="num mt-1.5 block break-all text-[0.6875rem] text-ink-mute">{investigationId}</span>
      </header>

      <ol className="divide-y divide-rule">
        {shown.length === 0 ? (
          <li className="px-4 py-3 text-sm text-ink-mute">{"Waiting for the first stage\u2026"}</li>
        ) : null}
        {shown.map((event) => (
          <li
            key={event.sequence}
            className="enter-row grid grid-cols-[4.75rem_5.5rem_minmax(0,1fr)] items-baseline gap-x-3 px-4 py-2 text-sm sm:grid-cols-[5.25rem_6rem_minmax(0,1fr)]"
          >
            <time
              dateTime={event.at}
              title={`${clockTime(event.at)} UTC — ${event.at}`}
              className="num text-[0.6875rem] font-medium text-ink-soft"
            >
              {(origin && offset(event.at, origin)) || clockTime(event.at)}
            </time>
            <span
              className={`text-xs font-semibold capitalize ${
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
