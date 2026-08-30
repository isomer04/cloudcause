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
    <section id="verdict" className="enter-panel overflow-hidden rounded-lg border border-rule bg-surface" aria-label="Verdict">
      <div className="px-5 pt-5 sm:px-6"><h2 className="dossier-label mb-4">Verdict</h2>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-mute">
        <span className="num">{report.investigation_id}</span>
        <span aria-hidden>·</span>
        <span>{range(report.current_period.start, report.current_period.end)}</span>
        <span aria-hidden>·</span>
        <span>generated {stamp(report.generated_at)}</span>
      </p>

      <h3 className="mt-3 max-w-[42ch] text-[1.75rem] font-semibold leading-[1.15] tracking-tight text-ink sm:text-[2rem]">
        {delta(report.total_absolute_change, currency)}
        <span className="text-ink-soft">
          {" "}
          across {report.provider_statuses.length || 1}{" "}
          {report.provider_statuses.length === 1 ? "provider" : "providers"}
        </span>
      </h3>
      <p className="num mt-1 text-sm text-ink-mute">
        {percent(report.total_percent_change)} versus the length-adjusted baseline
      </p>

      {report.summary ? (
        <p className="mt-3 max-w-[68ch] text-sm leading-relaxed text-ink-soft">
          {report.summary}
        </p>
      ) : null}

      </div>{/*
        The change itself is the headline above; repeating it here would be the
        third printing of one number on one screen. The strip carries the two
        levels it was computed from and how much of it the findings account for.
      */}<dl className="mt-5 grid grid-cols-2 border-y border-rule sm:grid-cols-3">
        <Figure label="This period" value={money(report.total_current_cost, currency)} />
        <Figure
          label="Baseline, length-adjusted"
          value={money(report.total_baseline_cost, currency)}
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
          emphasis
        />
      </dl>

      <div className="flex flex-wrap items-center gap-2 px-5 py-4 sm:px-6">
        <Tag>{ORIGIN_LABEL[report.data_origin] ?? "Fixture data"}</Tag>
        <Tag>
          {report.agent_mode === "stub" ? "Deterministic playbooks" : "Live AI agents"}
        </Tag>
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
    // `even:` outranks `sm:border-r` on specificity, so the even-cell suppression
    // has to be capped below the breakpoint or cell two loses its sm divider.
    <div className="border-r border-rule px-5 py-4 max-sm:even:border-r-0 last:border-r-0">
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
