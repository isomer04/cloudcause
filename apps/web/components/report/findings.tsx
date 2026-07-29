import { ConfidenceMeter, ProviderMark, RiskTag, SectionHeading, Tag } from "@/components/marks";
import { EvidenceTable } from "@/components/report/evidence-table";
import { day, delta, humanizeCategory, money, truncate } from "@/lib/format";
import type { Finding } from "@/lib/types";

export function Findings({
  findings,
  currency,
}: {
  findings: Finding[];
  currency: string;
}) {
  return (
    <section aria-label="Ranked findings">
      <SectionHeading
        aside={
          findings.length > 0 ? "Ranked by the cost each one explains" : undefined
        }
      >
        What caused it
      </SectionHeading>

      {findings.length === 0 ? (
        <p className="panel px-5 py-6 text-sm text-ink-soft">
          No finding cleared the evidence bar for this period. That is a result, not a failure:
          nothing crossed the materiality threshold, or nothing that did could be supported by
          evidence.
        </p>
      ) : (
        <div className="panel divide-y divide-rule">
          {findings.map((finding, index) => (
            <FindingEntry
              key={finding.finding_id}
              finding={finding}
              rank={index + 1}
              currency={currency}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function FindingEntry({
  finding,
  rank,
  currency,
}: {
  finding: Finding;
  rank: number;
  currency: string;
}) {
  return (
    <article className="grid grid-cols-[2rem_minmax(0,1fr)] gap-x-4 px-4 py-5 sm:grid-cols-[2.75rem_minmax(0,1fr)] sm:px-6">
      <p className="num pt-0.5 text-xl text-brand sm:text-2xl" aria-hidden>
        {String(rank).padStart(2, "0")}
      </p>

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <ProviderMark provider={finding.provider} />
          <span className="text-sm font-medium text-ink">
            {humanizeCategory(finding.category)}
          </span>
          <RiskTag risk={finding.risk} />
          {finding.is_uncertain ? <Tag tone="caution">uncertain</Tag> : null}
          <span className="num text-xs text-ink-mute">{finding.finding_id}</span>
        </div>

        <h3 className="mt-2 max-w-[70ch] font-display text-[1.0625rem] leading-snug text-ink">
          {finding.suspected_root_cause}
        </h3>

        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
          <div>
            <dt className="text-[0.6875rem] uppercase tracking-[0.04em] text-ink-soft">
              Cost impact
            </dt>
            <dd className="num mt-1 text-[0.9375rem] text-brand">
              {delta(finding.actual_cost_increase, currency)}
            </dd>
          </div>
          <div>
            <dt className="text-[0.6875rem] uppercase tracking-[0.04em] text-ink-soft">
              If unchanged, monthly
            </dt>
            <dd className="num mt-1 text-[0.9375rem] text-ink">
              {money(finding.estimated_monthly_impact, currency)}
            </dd>
          </div>
          <div>
            <dt className="text-[0.6875rem] uppercase tracking-[0.04em] text-ink-soft">
              Confidence
            </dt>
            <dd className="mt-1">
              <ConfidenceMeter value={finding.confidence} />
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-[0.6875rem] uppercase tracking-[0.04em] text-ink-soft">
              Resource
            </dt>
            <dd className="num mt-1 break-words text-[0.8125rem] text-ink">
              {finding.affected_resources.length > 0
                ? truncate(finding.affected_resources[0] ?? "", 34)
                : (finding.service_name ?? "\u2014")}
              {finding.affected_resources.length > 1 ? (
                <span className="text-ink-mute"> +{finding.affected_resources.length - 1}</span>
              ) : null}
            </dd>
          </div>
        </dl>

        {finding.recommendation ? (
          <div className="mt-4 rounded-md border border-brand-edge bg-brand-tint px-3.5 py-3">
            <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.04em] text-brand">
              Consider — needs human approval
            </p>
            <p className="mt-1 max-w-[70ch] text-sm leading-relaxed text-ink">
              {finding.recommendation}
            </p>
          </div>
        ) : null}

        {finding.warnings.length > 0 ? (
          <ul className="mt-3 space-y-1 text-xs text-caution">
            {finding.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        ) : null}

        {finding.applied_rules.length > 0 ? (
          <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
            {finding.applied_rules.map((rule) => (
              <li key={rule.rule_id} className="flex flex-wrap items-baseline gap-1.5">
                <a
                  href={rule.source_url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="num text-ink-soft underline decoration-rule-strong underline-offset-2 hover:text-brand hover:decoration-brand"
                >
                  {rule.rule_id}
                </a>
                <span className="text-ink-mute">
                  valid from {day(rule.valid_from)}, reviewed {day(rule.reviewed_at)}
                </span>
                {rule.is_stale ? <Tag tone="caution">stale</Tag> : null}
              </li>
            ))}
          </ul>
        ) : null}

        <details className="group mt-4">
          <summary className="inline-flex cursor-pointer items-center gap-1.5 rounded-sm text-sm text-ink-soft transition-colors duration-150 hover:text-brand">
            <span
              aria-hidden
              className="inline-block transition-transform duration-200 group-open:rotate-90"
            >
              ›
            </span>
            Evidence ({finding.evidence.length})
          </summary>
          <div className="mt-3">
            <EvidenceTable evidence={finding.evidence} />
          </div>
        </details>
      </div>
    </article>
  );
}
