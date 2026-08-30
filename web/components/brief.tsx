"use client";

import { useEffect, useState } from "react";

import { PROVIDER_LABEL } from "@/lib/format";
import type { InvestigationRequest, Provider, ScenarioSummary } from "@/lib/types";

const PROVIDERS: Provider[] = ["aws", "azure", "gcp"];

interface BriefProps {
  scenarios: ScenarioSummary[];
  scenarioId: string;
  request: InvestigationRequest;
  busy: boolean;
  /** Seconds left in a gateway-advertised 429 cooldown; 0 when not rate-limited. */
  retryCooldownSeconds?: number;
  /** Hidden when the brief runs over an uploaded dataset, which has no scenario. */
  showScenarioPicker?: boolean;
  /** False when no model key is configured, so a live run is not possible here. */
  liveAllowed?: boolean;
  onScenarioChange: (scenarioId: string) => void;
  onRequestChange: (request: InvestigationRequest) => void;
  onSubmit: () => void;
}

export function Brief({
  scenarios,
  scenarioId,
  request,
  busy,
  retryCooldownSeconds = 0,
  showScenarioPicker = true,
  liveAllowed = false,
  onScenarioChange,
  onRequestChange,
  onSubmit,
}: BriefProps) {
  const scenario = scenarios.find((entry) => entry.id === scenarioId);

  /**
   * Until React has hydrated, a click would submit the form natively and
   * reload the page instead of starting an investigation. Hold the button for
   * that first moment rather than losing the click.
   */
  const [ready, setReady] = useState(false);
  useEffect(() => setReady(true), []);

  function toggleProvider(provider: Provider) {
    const next = request.providers.includes(provider)
      ? request.providers.filter((entry) => entry !== provider)
      : [...PROVIDERS.filter((entry) => request.providers.includes(entry) || entry === provider)];
    onRequestChange({ ...request, providers: next });
  }

  return (
    <form
      className="panel px-5 py-5 sm:px-6"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          {showScenarioPicker ? (
            <div>
              <label htmlFor="scenario" className="field-label">
                Scenario
              </label>
              <select
                id="scenario"
                className="control mt-1.5"
                value={scenarioId}
                disabled={busy || scenarios.length === 0}
                onChange={(event) => onScenarioChange(event.target.value)}
              >
                {scenarios.length === 0 ? <option value="">No scenarios available</option> : null}
                {scenarios.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.title}
                  </option>
                ))}
              </select>
              {scenario ? (
                <p className="mt-1.5 text-xs text-ink-mute">
                  Seeded case <span className="num">{scenario.id}</span> · {scenario.category}
                </p>
              ) : null}
            </div>
          ) : request.dataset_id ? (
            <p className="text-xs text-ink-mute">
              Running over your sealed dataset{" "}
              <span className="num">{request.dataset_id}</span>. The dates below default to the
              period the data actually covers.
            </p>
          ) : null}

          <div>
            <label htmlFor="question" className="field-label">
              Question
            </label>
            <input
              id="question"
              className="control mt-1.5"
              value={request.question}
              disabled={busy}
              maxLength={280}
              onChange={(event) => onRequestChange({ ...request, question: event.target.value })}
            />
          </div>

          <fieldset disabled={busy}>
            <legend className="field-label">Investigation mode</legend>
            <div className="mt-1.5 grid gap-2 sm:grid-cols-2">
              <ModeButton
                active={request.agent_mode === "live"}
                disabled={!liveAllowed}
                title="Live AI agents"
                detail={
                  liveAllowed
                    ? "Uses hosted models and may incur API charges."
                    : "Needs a model API key on the server."
                }
                onClick={() => onRequestChange({ ...request, agent_mode: "live" })}
              />
              <ModeButton
                active={request.agent_mode === "stub"}
                title="Deterministic playbooks"
                detail="No model calls, no token charges."
                onClick={() => onRequestChange({ ...request, agent_mode: "stub" })}
              />
            </div>
            {liveAllowed ? null : (
              <p className="mt-1.5 text-xs leading-relaxed text-ink-mute">
                No model key is configured on this gateway. Add{" "}
                <span className="num">OPENAI_API_KEY</span> or{" "}
                <span className="num">GOOGLE_API_KEY</span> to its{" "}
                <span className="num">.env</span> and restart to enable live AI agents.
              </p>
            )}
          </fieldset>

          <fieldset disabled={busy}>
            <legend className="field-label">Providers</legend>
            <div className="mt-1.5 flex flex-wrap gap-2">
              {PROVIDERS.map((provider) => {
                const on = request.providers.includes(provider);
                return (
                  <button
                    key={provider}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggleProvider(provider)}
                    className={`rounded-sm border px-3 py-1.5 text-sm transition-colors duration-150 ${
                      on
                        ? "border-brand bg-brand text-on-brand"
                        : "border-rule-strong bg-surface text-ink-soft hover:border-brand-edge hover:text-ink"
                    } disabled:opacity-50`}
                  >
                    {PROVIDER_LABEL[provider]}
                  </button>
                );
              })}
            </div>
            {request.providers.length === 0 ? (
              <p className="mt-1.5 text-xs text-brand">Choose at least one provider.</p>
            ) : null}
          </fieldset>
        </div>

        <div className="space-y-4">
          <PeriodFields
            legend="Period under investigation"
            busy={busy}
            startId="start"
            endId="end"
            start={request.start_date}
            end={request.end_date}
            onStart={(value) => onRequestChange({ ...request, start_date: value })}
            onEnd={(value) => onRequestChange({ ...request, end_date: value })}
          />
          <PeriodFields
            legend="Baseline it is compared against"
            busy={busy}
            startId="baseline-start"
            endId="baseline-end"
            start={request.comparison_start_date}
            end={request.comparison_end_date}
            onStart={(value) => onRequestChange({ ...request, comparison_start_date: value })}
            onEnd={(value) => onRequestChange({ ...request, comparison_end_date: value })}
          />
          <p className="text-xs leading-relaxed text-ink-mute">
            Both ranges are inclusive billed days. The gateway length-adjusts the baseline, so
            unequal ranges are compared fairly rather than silently.
          </p>
        </div>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-rule pt-4">
        <button
          type="submit"
          disabled={busy || retryCooldownSeconds > 0 || !ready || request.providers.length === 0}
          className="rounded-sm bg-brand px-4 py-2 text-sm font-medium text-on-brand transition-colors duration-150 hover:bg-brand-press disabled:cursor-not-allowed disabled:opacity-45"
        >
          {busy
            ? "Investigating\u2026"
            : retryCooldownSeconds > 0
              ? `Try again in ${retryCooldownSeconds}s`
              : "Open investigation"}
        </button>
        <button
          type="button"
          disabled={busy || !scenario || !showScenarioPicker}
          onClick={() => onScenarioChange(scenarioId)}
          className="rounded-sm border border-rule-strong bg-surface px-4 py-2 text-sm text-ink-soft transition-colors duration-150 hover:border-brand-edge hover:text-ink disabled:opacity-45"
        >
          Reset to scenario
        </button>
        <span className="text-xs text-ink-mute">
          Nothing is changed in any cloud account. The report is a recommendation for a human.
        </span>
      </div>
    </form>
  );
}

function ModeButton({
  active,
  title,
  detail,
  onClick,
  disabled = false,
}: {
  active: boolean;
  title: string;
  detail: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      className={`rounded-sm border px-3 py-2 text-left transition-colors duration-150 ${
        active
          ? "border-brand bg-brand-tint text-ink"
          : "border-rule-strong bg-surface text-ink-soft hover:border-brand-edge"
      } disabled:cursor-not-allowed disabled:opacity-55 disabled:hover:border-rule-strong`}
    >
      <span className="block text-sm font-medium">{title}</span>
      <span className="mt-0.5 block text-xs text-ink-mute">{detail}</span>
    </button>
  );
}

function PeriodFields({
  legend,
  busy,
  startId,
  endId,
  start,
  end,
  onStart,
  onEnd,
}: {
  legend: string;
  busy: boolean;
  startId: string;
  endId: string;
  start: string;
  end: string;
  onStart: (value: string) => void;
  onEnd: (value: string) => void;
}) {
  return (
    <fieldset disabled={busy}>
      <legend className="field-label">{legend}</legend>
      <div className="mt-1.5 grid grid-cols-2 gap-2">
        <div>
          <label htmlFor={startId} className="sr-only">
            {legend} start
          </label>
          <input
            id={startId}
            type="date"
            className="control num"
            value={start}
            onChange={(event) => onStart(event.target.value)}
          />
        </div>
        <div>
          <label htmlFor={endId} className="sr-only">
            {legend} end
          </label>
          <input
            id={endId}
            type="date"
            className="control num"
            value={end}
            onChange={(event) => onEnd(event.target.value)}
          />
        </div>
      </div>
    </fieldset>
  );
}
