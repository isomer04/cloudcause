"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Brief } from "@/components/brief";
import { DataSource, type DataChoice } from "@/components/data-source";
import { GatewayWaking } from "@/components/gateway-waking";
import { ProgressLedger } from "@/components/progress-ledger";
import { ReportView } from "@/components/report-view";
import { client, GatewayError } from "@/lib/gateway-client";
import { NEW_INVESTIGATION_EVENT } from "@/lib/nav-events";
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
  agent_mode: "stub",
};

function withSelectedMode(
  next: InvestigationRequest,
  current?: InvestigationRequest,
): InvestigationRequest {
  return { ...next, agent_mode: current?.agent_mode ?? "stub" };
}

export function Console({
  scenarios,
  gatewayError,
  liveAllowed = false,
}: {
  scenarios: ScenarioSummary[];
  gatewayError: string | null;
  /** Whether this deployment permits hosted-model runs at all. */
  liveAllowed?: boolean;
}) {
  const first = scenarios[0];
  const [choice, setChoice] = useState<DataChoice>("demo");
  const [uploadReady, setUploadReady] = useState(false);
  const [scenarioId, setScenarioId] = useState(first?.id ?? "default");
  const [request, setRequest] = useState<InvestigationRequest>(
    first?.suggested_request
      ? withSelectedMode(first.suggested_request)
      : FALLBACK_REQUEST,
  );
  const [investigationId, setInvestigationId] = useState<string | null>(null);
  const [status, setStatus] = useState<InvestigationStatus | null>(null);
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [report, setReport] = useState<InvestigationReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retryAfterAt, setRetryAfterAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const sourceRef = useRef<EventSource | null>(null);

  const busy = status === "queued" || status === "running";

  // Ticks once a second only while a 429 cooldown is active, so the submit
  // button re-enables itself without a page reload once the wait is over.
  useEffect(() => {
    if (retryAfterAt === null) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [retryAfterAt]);

  const retryCooldownSeconds =
    retryAfterAt !== null ? Math.max(0, Math.ceil((retryAfterAt - now) / 1000)) : 0;

  useEffect(() => {
    if (retryAfterAt !== null && retryCooldownSeconds === 0) setRetryAfterAt(null);
  }, [retryAfterAt, retryCooldownSeconds]);

  const resetInvestigation = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
    setInvestigationId(null);
    setStatus(null);
    setEvents([]);
    setReport(null);
    setError(null);
  }, []);

  useEffect(() => {
    return () => sourceRef.current?.close();
  }, []);

  // "Investigate" in the rail is the way back to a blank brief from either page.
  // On /investigations/[id] it is an ordinary route change; here it has to clear
  // state the router cannot reach, so the rail asks and the console answers.
  useEffect(() => {
    const handle = () => {
      resetInvestigation();
      window.scrollTo({ top: 0 });
    };
    window.addEventListener(NEW_INVESTIGATION_EVENT, handle);
    return () => window.removeEventListener(NEW_INVESTIGATION_EVENT, handle);
  }, [resetInvestigation]);

  const applyScenario = useCallback(
    (id: string) => {
      setScenarioId(id);
      const scenario = scenarios.find((entry) => entry.id === id);
      if (scenario) setRequest((current) => withSelectedMode(scenario.suggested_request, current));
    },
    [scenarios],
  );

  /**
   * Switching data source rewrites the brief, because the demo dates mean nothing
   * over somebody else's export and a scenario id means nothing over a dataset.
   */
  const applyChoice = useCallback(
    (next: DataChoice) => {
      if (next !== choice) setUploadReady(false);
      setChoice(next);
      if (next === "demo") {
        setScenarioId("default");
        const demo = scenarios.find((entry) => entry.id === "default");
        setRequest((current) =>
          demo ? withSelectedMode(demo.suggested_request, current) : withSelectedMode(FALLBACK_REQUEST, current),
        );
      } else if (next === "scenario") {
        const seeded = scenarios.find((entry) => entry.id !== "default") ?? scenarios[0];
        if (seeded) {
          setScenarioId(seeded.id);
          setRequest((current) => withSelectedMode(seeded.suggested_request, current));
        }
      }
    },
    [choice, scenarios],
  );

  /** The gateway derives the brief from the sealed dataset; the UI derives nothing. */
  const applyDataset = useCallback((summary: DatasetSummary) => {
    if (summary.suggested_request) {
      setRequest((current) => withSelectedMode(summary.suggested_request!, current));
    }
    setUploadReady(true);
  }, []);

  const clearDataset = useCallback(() => {
    setUploadReady(false);
    setChoice("demo");
    setScenarioId("default");
    const demo = scenarios.find((entry) => entry.id === "default");
    setRequest((current) =>
      demo ? withSelectedMode(demo.suggested_request, current) : withSelectedMode(FALLBACK_REQUEST, current),
    );
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
    // A submission the UI itself disabled must never sneak through, since an
    // ambiguous retry during the gateway's cooldown could double-charge for
    // paid live-agent work.
    if (retryAfterAt !== null && Date.now() < retryAfterAt) return;
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
      setRetryAfterAt(null);
      setInvestigationId(created.investigation_id);
      subscribe(created.investigation_id);
    } catch (startError) {
      setStatus("failed");
      setError((startError as Error).message);
      if (startError instanceof GatewayError && startError.status === 429 && startError.retryAfterSeconds) {
        setRetryAfterAt(Date.now() + startError.retryAfterSeconds * 1000);
        setNow(Date.now());
      }
    }
  }, [request, scenarioId, subscribe, retryAfterAt]);

  const scenario = useMemo(
    () => scenarios.find((entry) => entry.id === scenarioId),
    [scenarioId, scenarios],
  );

  return (
    <div className="min-w-0">
      <header className="console-command flex flex-wrap items-center justify-between gap-3 px-5 py-2.5 lg:px-6">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="truncate text-xl font-semibold tracking-[-0.02em] text-ink">
              {report ? request.question : "New cost investigation"}
            </h1>
            <span className={`inline-flex items-center gap-1.5 text-[0.6875rem] font-semibold uppercase tracking-wider ${status === "failed" ? "text-brand" : report ? "text-verified" : "text-ink-mute"}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${status === "failed" ? "bg-brand" : report ? "bg-verified" : busy ? "bg-caution pulse-dot" : "bg-rule-strong"}`} />
              {report ? "Completed" : busy ? "Running" : "Draft"}
            </span>
          </div>
          <p className="num mt-0.5 text-[0.6875rem] text-ink-mute">
            {investigationId ?? "No case ID"} · {request.start_date} — {request.end_date}
          </p>
        </div>
        <p className="hidden text-[0.6875rem] text-ink-mute sm:block">Read-only · recommendations require human action</p>
      </header>

      {gatewayError ? <GatewayWaking detail={gatewayError} resource="scenarios" /> : null}

      <div id="investigation" className="console-workspace">
        <aside className="console-sidebar space-y-8 px-5 py-6 lg:px-6">
          <section id="source">
            <h2 className="dossier-label mb-4">01 / Data source</h2>
          {report && choice !== "upload" ? (
            <div className="space-y-3 border border-rule bg-sunken p-3 text-xs">
              <div className="flex items-center justify-between gap-3"><span className="text-ink-mute">Origin</span><span className="font-medium text-ink">{report.data_origin === "fixture" ? "Demo fixture" : report.data_origin === "upload" ? "Your upload" : "Live provider data"}</span></div>
              <div className="flex items-center justify-between gap-3"><span className="text-ink-mute">Providers</span><span className="num text-ink">{request.providers.join(" · ").toUpperCase()}</span></div>
              <div className="flex items-center justify-between gap-3"><span className="text-ink-mute">Mode</span><span className="text-ink">{report.agent_mode === "stub" ? "Playbooks" : "Live AI"}</span></div>
            </div>
          ) : (
          <DataSource
          choice={choice}
          busy={busy}
          seededScenarioCount={scenarios.filter((entry) => entry.id !== "default").length}
          onChoiceChange={applyChoice}
          onSealed={applyDataset}
          onCleared={clearDataset}
          detailsTargetId="upload-workspace"
          />
          )}
          </section>
          {investigationId ? (
            <ProgressLedger investigationId={investigationId} status={status ?? "queued"} events={events} error={error} />
          ) : (
            <div className="border-t border-rule pt-5 text-xs leading-relaxed text-ink-mute">
              Source data is normalized to FOCUS 1.4. Findings are published only when evidence and a versioned billing rule support them.
            </div>
          )}
        </aside>
        <div className="console-canvas">
          <section id="brief" className={report || (choice === "upload" && !uploadReady) ? "hidden" : "mx-auto max-w-4xl"}>
          <div className="mb-5">
            <h2 className="text-xl font-semibold tracking-[-0.02em] text-ink">Investigation brief</h2>
            <p className="mt-1 max-w-[68ch] text-sm text-ink-soft">Define the question and comparison window. CloudCause will correlate cost changes with available evidence without changing any cloud account.</p>
          </div>
          {error && !investigationId ? (
            <div className="mb-5 rounded-lg border border-brand-edge bg-brand-tint px-4 py-3.5">
              <p className="text-sm text-ink">{error}</p>
            </div>
          ) : null}
          <Brief
          scenarios={scenarios}
          scenarioId={scenarioId}
          request={request}
          busy={busy}
          retryCooldownSeconds={retryCooldownSeconds}
          showScenarioPicker={choice === "scenario"}
          liveAllowed={liveAllowed}
          onScenarioChange={applyScenario}
          onRequestChange={setRequest}
          onSubmit={() => void start()}
          />
          </section>
          <section className={!report && choice === "upload" && !uploadReady ? "mx-auto max-w-6xl" : "hidden"}>
              <div className="mb-5">
                <h2 className="text-xl font-semibold tracking-[-0.02em] text-ink">Upload and seal your data</h2>
                <p className="mt-1 max-w-[72ch] text-sm text-ink-soft">Add a cost export for each provider you want to investigate. Metrics, audit events, inventory, and recommendations strengthen causal confidence but remain optional.</p>
              </div>
              <div id="upload-workspace" className="panel p-5 sm:p-6" />
          </section>

      {/* Captions the picker, so it hides with the brief above it. */}
      {!report && scenario && scenario.id !== "default" && choice === "scenario" ? (
        <p className="mt-2.5 max-w-[80ch] text-xs leading-relaxed text-ink-mute">
          {scenario.title}
        </p>
      ) : null}

      {investigationId ? (
        <div className="mx-auto max-w-6xl space-y-6">
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
    </div>
    </div>
  );
}
