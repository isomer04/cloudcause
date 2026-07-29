"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Brief } from "@/components/brief";
import { DataSource, type DataChoice } from "@/components/data-source";
import { ProgressLedger } from "@/components/progress-ledger";
import { ReportView } from "@/components/report-view";
import { client } from "@/lib/gateway";
import type {
  DatasetSummary,
  InvestigationReport,
  InvestigationRequest,
  InvestigationStatus,
  ProgressEvent,
  ScenarioSummary,
} from "@/lib/types";

const FALLBACK_REQUEST: InvestigationRequest = {
  providers: ["aws", "azure", "gcp"],
  start_date: "2026-07-13",
  end_date: "2026-07-19",
  comparison_start_date: "2026-07-06",
  comparison_end_date: "2026-07-12",
  account_ids: [],
  question: "Why did our cloud spending increase last week?",
  scenario_id: "default",
  dataset_id: null,
};

export function Console({
  scenarios,
  gatewayError,
}: {
  scenarios: ScenarioSummary[];
  gatewayError: string | null;
}) {
  const first = scenarios[0];
  const [choice, setChoice] = useState<DataChoice>("demo");
  const [scenarioId, setScenarioId] = useState(first?.id ?? "default");
  const [request, setRequest] = useState<InvestigationRequest>(
    first?.suggested_request ?? FALLBACK_REQUEST,
  );
  const [investigationId, setInvestigationId] = useState<string | null>(null);
  const [status, setStatus] = useState<InvestigationStatus | null>(null);
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [report, setReport] = useState<InvestigationReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  const busy = status === "queued" || status === "running";

  useEffect(() => {
    return () => sourceRef.current?.close();
  }, []);

  const applyScenario = useCallback(
    (id: string) => {
      setScenarioId(id);
      const scenario = scenarios.find((entry) => entry.id === id);
      if (scenario) setRequest({ ...scenario.suggested_request });
    },
    [scenarios],
  );

  /**
   * Switching data source rewrites the brief, because the demo dates mean nothing
   * over somebody else's export and a scenario id means nothing over a dataset.
   */
  const applyChoice = useCallback(
    (next: DataChoice) => {
      setChoice(next);
      if (next === "demo") {
        setScenarioId("default");
        const demo = scenarios.find((entry) => entry.id === "default");
        setRequest(demo ? { ...demo.suggested_request } : FALLBACK_REQUEST);
      } else if (next === "scenario") {
        const seeded = scenarios.find((entry) => entry.id !== "default") ?? scenarios[0];
        if (seeded) {
          setScenarioId(seeded.id);
          setRequest({ ...seeded.suggested_request });
        }
      }
    },
    [scenarios],
  );

  /** The gateway derives the brief from the sealed dataset; the UI derives nothing. */
  const applyDataset = useCallback((summary: DatasetSummary) => {
    if (summary.suggested_request) setRequest({ ...summary.suggested_request });
  }, []);

  const clearDataset = useCallback(() => {
    setChoice("demo");
    setScenarioId("default");
    const demo = scenarios.find((entry) => entry.id === "default");
    setRequest(demo ? { ...demo.suggested_request } : FALLBACK_REQUEST);
  }, [scenarios]);

  /** Reconcile with the gateway once the stream ends, for any reason. */
  const settle = useCallback(async (id: string) => {
    try {
      const [trail, state] = await Promise.all([
        client.progress(id).catch(() => [] as ProgressEvent[]),
        client.state(id),
      ]);
      if (trail.length > 0) setEvents(trail);
      setStatus(state.status);
      setError(state.error);
      if (state.report) {
        setReport(state.report);
      } else if (state.status === "completed") {
        setReport(await client.report(id));
      }
    } catch (settleError) {
      setStatus("failed");
      setError((settleError as Error).message);
    }
  }, []);

  const subscribe = useCallback(
    (id: string) => {
      sourceRef.current?.close();
      const source = new EventSource(client.eventsUrl(id));
      sourceRef.current = source;

      source.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as ProgressEvent;
          setStatus((current) => (current === "completed" ? current : "running"));
          setEvents((current) =>
            current.some((entry) => entry.sequence === event.sequence)
              ? current
              : [...current, event],
          );
        } catch {
          /* A malformed frame must not take the run down; settle() reconciles. */
        }
      };

      source.addEventListener("close", () => {
        source.close();
        void settle(id);
      });

      source.onerror = () => {
        source.close();
        void settle(id);
      };
    },
    [settle],
  );

  const start = useCallback(async () => {
    setError(null);
    setReport(null);
    setEvents([]);
    setStatus("queued");
    try {
      const created = await client.start(
        request.dataset_id
          ? { ...request, scenario_id: "" }
          : { ...request, scenario_id: scenarioId, dataset_id: null },
      );
      setInvestigationId(created.investigation_id);
      subscribe(created.investigation_id);
    } catch (startError) {
      setStatus("failed");
      setError((startError as Error).message);
    }
  }, [request, scenarioId, subscribe]);

  const scenario = useMemo(
    () => scenarios.find((entry) => entry.id === scenarioId),
    [scenarioId, scenarios],
  );

  return (
    <div className="mx-auto w-full max-w-6xl px-5 py-7 sm:px-8 lg:py-10">
      <header className="max-w-[68ch]">
        <h1 className="font-display text-[1.75rem] leading-tight text-ink">
          Why did the bill go up?
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-ink-soft">
          Pick a period, name the question, and CloudCause investigates AWS, Azure, and Google Cloud
          in parallel. Every conclusion arrives with the evidence IDs behind it and the billing rule
          that was applied, or it is dropped.
        </p>
      </header>

      {gatewayError ? (
        <div className="mt-5 rounded-lg border border-brand-edge bg-brand-tint px-4 py-3.5">
          <h2 className="text-sm font-semibold text-brand">The gateway is not answering</h2>
          <p className="mt-1 text-sm text-ink">
            {gatewayError} Start it with <span className="num">uv run cloudcause-api</span>, then
            reload.
          </p>
        </div>
      ) : null}

      <div id="investigation" className="mt-6 space-y-4">
        <DataSource
          choice={choice}
          busy={busy}
          onChoiceChange={applyChoice}
          onSealed={applyDataset}
          onCleared={clearDataset}
        />
        <Brief
          scenarios={scenarios}
          scenarioId={scenarioId}
          request={request}
          busy={busy}
          showScenarioPicker={choice === "scenario"}
          onScenarioChange={applyScenario}
          onRequestChange={setRequest}
          onSubmit={() => void start()}
        />
      </div>

      {scenario && scenario.id !== "default" && choice === "scenario" ? (
        <p className="mt-2.5 max-w-[80ch] text-xs leading-relaxed text-ink-mute">
          {scenario.title}
        </p>
      ) : null}

      {investigationId ? (
        <div className="mt-7 space-y-8">
          <ProgressLedger
            investigationId={investigationId}
            status={status ?? "queued"}
            events={events}
            error={error}
          />

          {report ? (
            <ReportView report={report} />
          ) : busy ? (
            <p className="text-sm text-ink-mute">
              Normalizing provider exports to FOCUS 1.4, then comparing periods. The report appears
              when the evidence has been validated.
            </p>
          ) : null}

          {report ? (
            <p className="text-xs text-ink-mute">
              Kept in history as{" "}
              <Link
                href={`/investigations/${report.investigation_id}`}
                className="num underline decoration-rule-strong underline-offset-2 hover:text-brand"
              >
                {report.investigation_id}
              </Link>
              .
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
