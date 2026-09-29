import { ProviderMark, SectionHeading } from "@/components/marks";
import { day, delta, money, percent } from "@/lib/format";
import type { PeriodComparison, ProviderComparison } from "@/lib/types";

const VIEW_WIDTH = 320;
const VIEW_HEIGHT = 88;

/**
 * Daily effective cost, baseline against the period under investigation.
 *
 * Every value plotted here is a figure the gateway's analytics layer already
 * produced. The only arithmetic is the vertical scale.
 */
export function DailyShape({ comparison }: { comparison: PeriodComparison }) {
  if (comparison.providers.length === 0) return null;

  return (
    <section aria-label="Daily cost shape">
      <SectionHeading
        aside={
          <span className="flex items-center gap-3">
            <Legend swatch="bg-rule-strong" label="Baseline" />
            <Legend swatch="bg-brand" label="This period" />
          </span>
        }
      >
        Where the money moved
      </SectionHeading>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {comparison.providers.map((provider) => (
          <ProviderShape key={provider.provider} comparison={provider} />
        ))}
      </div>
    </section>
  );
}

function Legend({ swatch, label }: { swatch: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span aria-hidden className={`h-2 w-2 rounded-[1px] ${swatch}`} />
      {label}
    </span>
  );
}

function ProviderShape({ comparison }: { comparison: ProviderComparison }) {
  const baseline = comparison.daily_baseline.map((entry) => entry.effective_cost);
  const current = comparison.daily_current.map((entry) => entry.effective_cost);
  const columns = Math.max(baseline.length, current.length, 1);
  const peak = Math.max(...baseline, ...current, 0.0001);

  const slot = VIEW_WIDTH / columns;
  const barWidth = Math.max(slot * 0.32, 1.5);
  const gap = slot * 0.12;

  return (
    <figure className="panel px-4 py-3.5">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <ProviderMark provider={comparison.provider} full />
        <span className="num text-sm text-brand">
          {delta(comparison.absolute_change, comparison.currency)}
        </span>
      </figcaption>

      <svg
        className="mt-3 h-24 w-full"
        viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${comparison.provider} daily effective cost: baseline totalling ${money(
          comparison.baseline_cost,
          comparison.currency,
        )}, this period totalling ${money(comparison.current_cost, comparison.currency)}`}
      >
        <line
          x1="0"
          y1={VIEW_HEIGHT - 0.5}
          x2={VIEW_WIDTH}
          y2={VIEW_HEIGHT - 0.5}
          stroke="var(--color-rule-strong)"
          strokeWidth="1"
        />
        {Array.from({ length: columns }, (_, index) => {
          const baseValue = baseline[index] ?? 0;
          const currentValue = current[index] ?? 0;
          const baseHeight = (baseValue / peak) * (VIEW_HEIGHT - 6);
          const currentHeight = (currentValue / peak) * (VIEW_HEIGHT - 6);
          const left = index * slot + gap;
          return (
            <g key={index}>
              <rect
                x={left}
                y={VIEW_HEIGHT - baseHeight}
                width={barWidth}
                height={baseHeight}
                fill="var(--color-rule-strong)"
              />
              <rect
                x={left + barWidth + gap}
                y={VIEW_HEIGHT - currentHeight}
                width={barWidth}
                height={currentHeight}
                fill="var(--color-brand)"
              />
            </g>
          );
        })}
      </svg>

      <div className="mt-2 flex items-baseline justify-between text-[0.6875rem] text-ink-mute">
        <span className="num">{day(comparison.current_period.start)}</span>
        <span className="num">{day(comparison.current_period.end)}</span>
      </div>

      <dl className="mt-2.5 space-y-1 border-t border-rule pt-2.5 text-xs">
        <Row
          label="This period"
          value={money(comparison.current_cost, comparison.currency)}
        />
        <Row
          label="Baseline"
          value={money(comparison.baseline_cost, comparison.currency)}
          note={percent(comparison.percent_change)}
        />
        <Row
          label="Material candidates"
          value={`${comparison.candidates.length}`}
        />
      </dl>
    </figure>
  );
}

function Row({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-ink-mute">{label}</dt>
      <dd className="flex items-baseline gap-2">
        {note ? <span className="text-ink-mute">{note}</span> : null}
        <span className="num text-ink">{value}</span>
      </dd>
    </div>
  );
}
