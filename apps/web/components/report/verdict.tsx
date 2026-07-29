import { Tag } from "@/components/marks";
import { delta, money, percent, range, stamp } from "@/lib/format";
import type { DataOrigin, InvestigationReport } from "@/lib/types";

/**
 * Where the numbers came from, keyed off `data_origin` rather than `data_mode`:
 * `data_mode` describes the process, so an uploaded run in a fixtures-mode
 * gateway would otherwise be labelled fixture data and read as verified.
 */
const ORIGIN_LABEL: Record<DataOrigin, string> = {
  fixture: "Fixture data",
  upload: "Your uploaded export",
  live: "Live provider data",
};

/**
 * The verdict, as a sentence, before any chart. Numbers follow in a ruled row
 * because a reader who has to defend this in a meeting needs the figures
 * legible and copyable, not enlarged.
 */
export function Verdict({ report }: { report: InvestigationReport }) {
  const currency = report.currency;
  const attributed = report.reconciliation?.attributed_change;
  const unattributed = report.reconciliation?.unattributed_change ?? 0;

  return (
    <section className="enter-panel" aria-label="Verdict">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-mute">
        <span className="num">{report.investigation_id}</span>
        <span aria-hidden>·</span>
        <span>{range(report.current_period.start, report.current_period.end)}</span>
        <span aria-hidden>·</span>
        <span>generated {stamp(report.generated_at)}</span>
      </p>

      <h1 className="mt-2 max-w-[42ch] font-display text-hero text-ink">
        {delta(report.total_absolute_change, currency)}
        <span className="text-ink-soft">
          {" "}
          across {report.provider_statuses.length || 1}{" "}
          {report.provider_statuses.length === 1 ? "provider" : "providers"}
        </span>
      </h1>

      {report.summary ? (
        <p className="mt-3 max-w-[68ch] font-display text-[1.0625rem] leading-relaxed text-ink-soft">
          {report.summary}
        </p>
      ) : null}

      <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 border-y border-rule py-4 sm:grid-cols-4">
        <Figure label="This period" value={money(report.total_current_cost, currency)} />
        <Figure
          label="Baseline, length-adjusted"
          value={money(report.total_baseline_cost, currency)}
        />
        <Figure
          label="Change"
          value={delta(report.total_absolute_change, currency)}
          note={percent(report.total_percent_change)}
          emphasis
        />
        <Figure
          label="Explained by findings"
          value={attributed === undefined ? "n/a" : delta(attributed, currency)}
          note={
            report.reconciliation
              ? report.reconciliation.within_tolerance
                ? `${delta(unattributed, currency)} unattributed, within tolerance`
                : `${delta(unattributed, currency)} unattributed`
              : undefined
          }
        />
      </dl>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Tag>{ORIGIN_LABEL[report.data_origin] ?? "Fixture data"}</Tag>
        <Tag>{report.agent_mode === "stub" ? "Deterministic agents" : "Live agents"}</Tag>
        <Tag>FOCUS {report.knowledge?.focus_version ?? "unknown"}</Tag>
        <Tag>
          {report.findings.length} {report.findings.length === 1 ? "finding" : "findings"}
        </Tag>
        <Tag>
          {report.findings.reduce((total, finding) => total + finding.evidence.length, 0)} evidence
          items
        </Tag>
      </div>
    </section>
  );
}

function Figure({
  label,
  value,
  note,
  emphasis = false,
}: {
  label: string;
  value: string;
  note?: string;
  emphasis?: boolean;
}) {
  return (
    <div>
      <dt className="text-[0.6875rem] font-semibold uppercase tracking-[0.04em] text-ink-soft">
        {label}
      </dt>
      <dd
        className={`num mt-1 text-[1.0625rem] leading-tight ${emphasis ? "text-brand" : "text-ink"}`}
      >
        {value}
      </dd>
      {note ? <dd className="mt-0.5 text-xs text-ink-mute">{note}</dd> : null}
    </div>
  );
}
