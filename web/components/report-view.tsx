import { DailyShape } from "@/components/report/daily-shape";
import { Exports } from "@/components/report/exports";
import { Findings } from "@/components/report/findings";
import { Provenance } from "@/components/report/provenance";
import { Verdict } from "@/components/report/verdict";
import { Tag } from "@/components/marks";
import type { InvestigationReport } from "@/lib/types";

export function ReportView({ report }: { report: InvestigationReport }) {
  const unsupported = report.validation_issues.some(
    (issue) => issue.code === "cause_unsupported_by_available_sources",
  );
  return (
    <div className="report-workspace">
      <div className="min-w-0 space-y-6">
      <Verdict report={report} />

      {report.data_origin === "upload" ? (
        <section
          aria-label="Data origin"
          className="rounded-lg border border-caution/40 bg-caution-tint px-4 py-3.5"
        >
          <h2 className="text-sm font-semibold text-caution">
            Measured from your export, not verified
          </h2>
          <p className="mt-1 max-w-[80ch] text-sm text-ink">
            Every figure below was computed by deterministic code from the cost export you
            supplied, and each conclusion cites a versioned billing rule. CloudCause did not read
            any cloud account, so it cannot confirm that the export is complete or current.
            {unsupported
              ? " No metrics, audit events, inventory, or provider recommendations were supplied, so the cost changes are measured but the mechanisms behind them are not confirmed: findings are published as unexplained increases."
              : ""}
          </p>
        </section>
      ) : null}

      {report.warnings.length > 0 ? (
        <section
          aria-label="Warnings"
          className="rounded-lg border border-caution/40 bg-caution-tint px-4 py-3.5"
        >
          <h2 className="text-sm font-semibold text-caution">
            {report.warnings.length === 1 ? "One caveat" : `${report.warnings.length} caveats`}
          </h2>
          <ul className="mt-1.5 space-y-1 text-sm text-ink">
            {report.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <Findings findings={report.findings} currency={report.currency} />

      {report.comparison ? <DailyShape comparison={report.comparison} /> : null}
      </div>
      <aside className="evidence-inspector min-w-0 space-y-5 border border-rule bg-surface p-5 xl:sticky xl:top-20" aria-label="Evidence and provenance inspector">
        <div>
          <p className="dossier-label">Evidence inspector</p>
          <p className="mt-2 text-sm font-semibold text-ink">{report.findings[0]?.suspected_root_cause ?? "No supported finding"}</p>
          <p className="num mt-2 text-xs text-ink-mute">{report.findings[0]?.finding_id ?? report.investigation_id}</p>
        </div>
        {report.findings[0]?.evidence[0] ? (
          <div className="border-t border-rule pt-4 text-xs leading-relaxed text-ink-soft">
            <p className="dossier-label mb-2">Selected observation</p>
            {report.findings[0].evidence[0].statement}
            {report.findings[0].evidence[0].contains_untrusted_text ? (
              <span className="ml-2 align-middle">
                <Tag tone="caution">untrusted text</Tag>
              </span>
            ) : null}
          </div>
        ) : null}
        {/*
          Four sections of audit trail after the reader already has the answer
          buries the answer. The click is the proof: whoever wants the rule
          versions, review dates, and fifteen data sources will open this, and
          nobody else has to scroll past them.
        */}
        <details className="group border-t border-rule pt-4">
          <summary className="flex cursor-pointer items-center gap-1.5 text-sm text-ink-soft transition-colors duration-150 hover:text-brand">
            <span
              aria-hidden
              className="inline-block transition-transform duration-200 group-open:rotate-90"
            >
              ›
            </span>
            Provenance &amp; method
            <span className="num ml-auto text-xs text-ink-mute">
              contract {report.contract_version}
            </span>
          </summary>
          <div className="mt-4">
            <Provenance report={report} compact heading={false} />
          </div>
        </details>
        <Exports investigationId={report.investigation_id} />
      </aside>
    </div>
  );
}
