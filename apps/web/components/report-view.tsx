import { DailyShape } from "@/components/report/daily-shape";
import { Exports } from "@/components/report/exports";
import { Findings } from "@/components/report/findings";
import { Provenance } from "@/components/report/provenance";
import { Verdict } from "@/components/report/verdict";
import type { InvestigationReport } from "@/lib/types";

export function ReportView({ report }: { report: InvestigationReport }) {
  const unsupported = report.validation_issues.some(
    (issue) => issue.code === "cause_unsupported_by_available_sources",
  );
  return (
    <div className="space-y-8">
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

      <Provenance report={report} />

      <Exports investigationId={report.investigation_id} />
    </div>
  );
}
