import { OriginText, ProviderMark, SectionHeading, Tag, WorkerStatusText } from "@/components/marks";
import { day, delta, money, stamp } from "@/lib/format";
import type { InvestigationReport } from "@/lib/types";

/**
 * Provenance is not an appendix in this product. A conclusion is only as good
 * as the freshness of the data and the review date of the billing rule behind
 * it, so both are first-class and always shown.
 */
export function Provenance({ report }: { report: InvestigationReport }) {
  const knowledge = report.knowledge;
  const reconciliation = report.reconciliation;

  return (
    <section aria-label="Provenance and freshness">
      <SectionHeading aside={`contract ${report.contract_version}`}>
        How far to trust this
      </SectionHeading>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="panel overflow-hidden">
          <h3 className="border-b border-rule px-4 py-2.5 text-sm font-medium text-ink">
            Provider specialists
          </h3>
          <div className="overflow-x-auto">
            <table className="hairline-table">
              <thead>
                <tr>
                  <th scope="col">Provider</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">Findings</th>
                  <th scope="col">Data through</th>
                  <th scope="col">Took</th>
                  <th scope="col">Origin</th>
                </tr>
              </thead>
              <tbody>
                {report.provider_statuses.map((status) => (
                  <tr key={status.provider}>
                    <td>
                      <ProviderMark provider={status.provider} />
                    </td>
                    <td>
                      <WorkerStatusText status={status.status} />
                      {status.message ? (
                        <span className="block text-xs text-ink-mute">{status.message}</span>
                      ) : null}
                    </td>
                    <td className="num">{status.finding_count}</td>
                    <td className="num whitespace-nowrap text-ink-soft">
                      {stamp(status.data_through)}
                    </td>
                    <td className="num whitespace-nowrap text-ink-mute">
                      {status.duration_seconds.toFixed(1)}s
                    </td>
                    <td>
                      <OriginText origin={status.origin} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="panel overflow-hidden">
          <h3 className="border-b border-rule px-4 py-2.5 text-sm font-medium text-ink">
            Billing knowledge
          </h3>
          <dl className="divide-y divide-rule text-sm">
            <Line label="FOCUS version" value={knowledge?.focus_version ?? "unknown"} />
            <Line
              label="Rules applied"
              value={
                knowledge && knowledge.rule_ids.length > 0 ? (
                  <span className="flex flex-wrap gap-1.5">
                    {knowledge.rule_ids.map((id) => (
                      <span key={id} className="num text-ink">
                        {id}
                      </span>
                    ))}
                  </span>
                ) : (
                  "none"
                )
              }
            />
            <Line
              label="Review dates"
              value={
                knowledge
                  ? `${day(knowledge.oldest_review_date)} to ${day(knowledge.newest_review_date)}`
                  : "unknown"
              }
            />
            <Line
              label="Stale rules"
              value={
                knowledge && knowledge.stale_rule_ids.length > 0 ? (
                  <span className="flex flex-wrap gap-1.5">
                    {knowledge.stale_rule_ids.map((id) => (
                      <Tag key={id} tone="caution">
                        {id}
                      </Tag>
                    ))}
                  </span>
                ) : (
                  "none"
                )
              }
            />
            {reconciliation ? (
              <Line
                label="Reconciliation"
                value={
                  <span className="flex flex-wrap items-baseline gap-x-2">
                    <span className="num text-ink">
                      {delta(reconciliation.attributed_change, report.currency)} of{" "}
                      {delta(reconciliation.total_change, report.currency)}
                    </span>
                    <Tag tone={reconciliation.within_tolerance ? "savings" : "caution"}>
                      {reconciliation.within_tolerance ? "within tolerance" : "outside tolerance"}
                    </Tag>
                  </span>
                }
              />
            ) : null}
          </dl>
        </div>
      </div>

      {report.sources.length > 0 ? (
        <details className="panel mt-4 overflow-hidden">
          <summary className="cursor-pointer px-4 py-2.5 text-sm font-medium text-ink">
            Data sources ({report.sources.length})
          </summary>
          <div className="overflow-x-auto border-t border-rule">
            <table className="hairline-table">
              <thead>
                <tr>
                  <th scope="col">Provider</th>
                  <th scope="col">Source</th>
                  <th scope="col">Data through</th>
                  <th scope="col">Retrieved</th>
                  <th scope="col">Origin</th>
                </tr>
              </thead>
              <tbody>
                {report.sources.map((source, index) => (
                  <tr key={`${source.provider}-${source.source}-${index}`}>
                    <td>
                      <ProviderMark provider={source.provider} />
                    </td>
                    <td className="num text-ink-soft">{source.source}</td>
                    <td className="num whitespace-nowrap">{stamp(source.data_through)}</td>
                    <td className="num whitespace-nowrap text-ink-mute">
                      {stamp(source.retrieved_at)}
                    </td>
                    <td>
                      <OriginText origin={source.origin} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}

      {report.validation_issues.length > 0 ? (
        <div className="panel mt-4 px-4 py-3.5">
          <h3 className="text-sm font-medium text-ink">Evidence validation</h3>
          <ul className="mt-2 space-y-1.5 text-sm">
            {report.validation_issues.map((issue, index) => (
              <li key={`${issue.code}-${index}`} className="flex flex-wrap items-baseline gap-2">
                <Tag tone={issue.severity === "info" ? "neutral" : "caution"}>{issue.severity}</Tag>
                <span className="num text-xs text-ink-mute">{issue.code}</span>
                <span className="text-ink">{issue.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <p className="mt-4 text-xs text-ink-mute">
        Totals are computed by deterministic code, not by a model. Cost figures cannot be inflated,
        deflated, or invented by an agent. Spend in this report:{" "}
        <span className="num">{money(report.total_current_cost, report.currency)}</span>.
      </p>
    </section>
  );
}

function Line({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-4 py-2.5">
      <dt className="text-ink-mute">{label}</dt>
      <dd className="text-right text-ink">{value}</dd>
    </div>
  );
}
