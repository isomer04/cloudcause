"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { ProviderMark, Tag } from "@/components/marks";
import { GatewayError, client } from "@/lib/gateway-client";
import { PROVIDER_LABEL } from "@/lib/format";
import type {
  DatasetCreated,
  DatasetIngestReport,
  DatasetSourceKind,
  DatasetSummary,
  Provider,
} from "@/lib/types";

export type DataChoice = "demo" | "scenario" | "upload";

const PROVIDERS: Provider[] = ["aws", "azure", "gcp"];

const KINDS: { kind: DatasetSourceKind; label: string; hint: string; required?: boolean }[] = [
  {
    kind: "cost",
    label: "Cost export",
    hint: "AWS CUR 2.0, Azure Cost Management query result, or a GCP billing export",
    required: true,
  },
  { kind: "metrics", label: "Metrics", hint: "CloudCause metric series" },
  { kind: "audit", label: "Audit events", hint: "CloudCause audit events" },
  { kind: "inventory", label: "Inventory", hint: "CloudCause resources" },
  { kind: "recommendations", label: "Recommendations", hint: "CloudCause recommendations" },
];

type SlotState =
  | { status: "empty" }
  | { status: "uploading" }
  | { status: "accepted"; report: DatasetIngestReport }
  | { status: "rejected"; detail: string };

type Slots = Partial<Record<string, SlotState>>;

const slotKey = (provider: Provider, kind: DatasetSourceKind) => `${provider}/${kind}`;

interface DataSourceProps {
  choice: DataChoice;
  busy: boolean;
  onChoiceChange: (choice: DataChoice) => void;
  /** Called once the dataset is sealed, with the brief the gateway derived from it. */
  onSealed: (summary: DatasetSummary) => void;
  /** Called when the dataset is discarded, so the brief goes back to the demo. */
  onCleared: () => void;
  /** Moves the upload workspace into a larger task area while keeping selector state local. */
  detailsTargetId?: string;
  /** Seeded scenarios the gateway actually serves, so the label cannot go stale. */
  seededScenarioCount?: number;
}

export function DataSource({
  choice,
  busy,
  onChoiceChange,
  onSealed,
  onCleared,
  detailsTargetId,
  seededScenarioCount,
}: DataSourceProps) {
  const [dataset, setDataset] = useState<DatasetCreated | null>(null);
  const [slots, setSlots] = useState<Slots>({});
  const [sealed, setSealed] = useState<DatasetSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [detailsTarget, setDetailsTarget] = useState<HTMLElement | null>(null);
  const creating = useRef<Promise<DatasetCreated> | null>(null);

  useEffect(() => {
    setDetailsTarget(detailsTargetId ? document.getElementById(detailsTargetId) : null);
  }, [detailsTargetId]);

  const ensureDataset = useCallback(async (): Promise<DatasetCreated> => {
    if (dataset) return dataset;
    if (!creating.current) {
      // Cleared on failure as well as on success: leaving a rejected promise
      // cached would make every later upload fail with the first error.
      creating.current = client
        .createDataset()
        .then((created) => {
          setDataset(created);
          creating.current = null;
          return created;
        })
        .catch((error) => {
          creating.current = null;
          throw error;
        });
    }
    return creating.current;
  }, [dataset]);

  const upload = useCallback(
    async (provider: Provider, kind: DatasetSourceKind, file: File) => {
      setError(null);
      setSlots((current) => ({ ...current, [slotKey(provider, kind)]: { status: "uploading" } }));
      try {
        const target = await ensureDataset();
        const report = await client.putSource(target.dataset_id, provider, kind, file);
        setSlots((current) => ({
          ...current,
          [slotKey(provider, kind)]: { status: "accepted", report },
        }));
      } catch (uploadError) {
        const detail =
          uploadError instanceof GatewayError
            ? uploadError.message
            : (uploadError as Error).message;
        setSlots((current) => ({
          ...current,
          [slotKey(provider, kind)]: { status: "rejected", detail },
        }));
      }
    },
    [ensureDataset],
  );

  const seal = useCallback(async () => {
    if (!dataset) return;
    setWorking(true);
    setError(null);
    try {
      const summary = await client.sealDataset(dataset.dataset_id);
      setSealed(summary);
      onSealed(summary);
    } catch (sealError) {
      setError((sealError as Error).message);
    } finally {
      setWorking(false);
    }
  }, [dataset, onSealed]);

  const discard = useCallback(async () => {
    let message: string | null = null;
    if (dataset) {
      try {
        await client.deleteDataset(dataset.dataset_id);
      } catch (deleteError) {
        // A 404 means it is already gone, which is the outcome we wanted.
        // Anything else is surfaced, but the local reset still happens: the user
        // asked to start over and must not be stuck holding a dead dataset id.
        const failure = deleteError as GatewayError;
        if (!(failure instanceof GatewayError) || failure.status !== 404) {
          message = `the dataset could not be deleted on the server (${failure.message}). It expires on its own; starting over locally.`;
        }
      }
    }
    setDataset(null);
    setSlots({});
    setSealed(null);
    setError(message);
    creating.current = null;
    onCleared();
  }, [dataset, onCleared]);

  const acceptedCost = PROVIDERS.some(
    (provider) => slots[slotKey(provider, "cost")]?.status === "accepted",
  );

  const uploadWorkspace = choice === "upload" ? (
    <div className="space-y-4">
      <Privacy expiresAt={dataset?.expires_at ?? null} sealed={sealed !== null} />

      {sealed ? (
        <SealedSummary summary={sealed} onDiscard={() => void discard()} busy={busy} />
      ) : (
        <>
          <div className="grid gap-4 xl:grid-cols-3">
            {PROVIDERS.map((provider) => (
              <ProviderZone
                key={provider}
                provider={provider}
                slots={slots}
                busy={busy || working}
                onFile={(kind, file) => void upload(provider, kind, file)}
              />
            ))}
          </div>
          <Templates />
          {error ? <p className="text-sm text-brand">{error}</p> : null}
          <div className="flex flex-wrap items-center gap-3 border-t border-rule pt-4">
            <button type="button" disabled={busy || working || !acceptedCost} onClick={() => void seal()} className="rounded-sm bg-brand px-4 py-2 text-sm font-medium text-on-brand transition-colors duration-150 hover:bg-brand-press disabled:cursor-not-allowed disabled:opacity-45">
              {working ? "Sealing\u2026" : "Use this data"}
            </button>
            {dataset ? <button type="button" disabled={busy || working} onClick={() => void discard()} className="rounded-sm border border-rule-strong bg-surface px-4 py-2 text-sm text-ink-soft transition-colors duration-150 hover:border-brand-edge hover:text-ink disabled:opacity-45">Delete now</button> : null}
            <span className="text-xs text-ink-mute">{acceptedCost ? "Sealing freezes the dataset so every part of the investigation reads the same rows." : "At least one accepted cost export is needed: without it there is no period to compare."}</span>
          </div>
        </>
      )}
    </div>
  ) : null;

  return (
    <section aria-label="Data source">
      <fieldset disabled={busy}>
        <legend className="field-label">Where the numbers come from</legend>
        <div className="mt-2 flex flex-wrap gap-2">
          <ChoiceButton
            active={choice === "demo"}
            onClick={() => onChoiceChange("demo")}
            title="Demo — multi-cloud"
            subtitle="Three planted causes across AWS, Azure, and Google Cloud"
          />
          <ChoiceButton
            active={choice === "scenario"}
            onClick={() => onChoiceChange("scenario")}
            title="Demo — one planted cause"
            subtitle={`${seededScenarioCount ?? "Seeded"} seeded scenarios, one provider each`}
          />
          <ChoiceButton
            active={choice === "upload"}
            onClick={() => onChoiceChange("upload")}
            title="Your data"
            subtitle="Your own cost export, parsed and then discarded"
          />
        </div>
      </fieldset>

      {choice === "upload" && sealed && detailsTargetId ? (
        <div className="mt-4 rounded-sm border border-verified/30 bg-verified/5 px-3 py-2.5">
          <p className="text-xs font-medium text-verified">Dataset accepted and sealed</p>
          <p className="mt-1 text-xs leading-relaxed text-ink-mute">
            The investigation brief is ready. Review it in the main panel, then select Open investigation.
          </p>
        </div>
      ) : null}

      {choice === "upload" && !detailsTargetId ? (
        <div className="mt-5 space-y-4 border-t border-rule pt-4">
          {uploadWorkspace}
        </div>
      ) : null}
      {detailsTarget && uploadWorkspace ? createPortal(uploadWorkspace, detailsTarget) : null}
    </section>
  );
}

function ChoiceButton({
  active,
  onClick,
  title,
  subtitle,
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  subtitle: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`min-w-40 flex-1 rounded-md border px-3 py-2.5 text-left transition-colors duration-150 ${
        active
          ? "border-brand bg-brand-tint text-ink"
          : "border-rule-strong bg-surface text-ink-soft hover:border-brand-edge hover:text-ink"
      } disabled:opacity-50`}
    >
      <span className="block text-sm font-medium">{title}</span>
      <span className="mt-0.5 block text-xs text-ink-mute">{subtitle}</span>
    </button>
  );
}

function Privacy({ expiresAt, sealed }: { expiresAt: string | null; sealed: boolean }) {
  return (
    <p className="max-w-[80ch] text-xs leading-relaxed text-ink-soft">
      Your file is parsed on the server and the raw bytes are discarded: nothing is written to disk
      and no row is ever logged. Only the normalized daily rows are kept, for two hours
      {expiresAt ? (
        <>
          {" "}
          (until <span className="num">{expiresAt.replace("T", " ").slice(0, 19)} UTC</span>)
        </>
      ) : null}
      , and then deleted. <strong className="font-medium text-ink">Delete now</strong> removes them
      immediately. The report outlives the data{sealed ? "" : " that produced it"}. This deployment
      has no authentication, so do not point a shared instance at data you would not show its other
      users.
    </p>
  );
}

function ProviderZone({
  provider,
  slots,
  busy,
  onFile,
}: {
  provider: Provider;
  slots: Slots;
  busy: boolean;
  onFile: (kind: DatasetSourceKind, file: File) => void;
}) {
  const touched = KINDS.some(({ kind }) => slots[slotKey(provider, kind)]);
  const costState = slots[slotKey(provider, "cost")];
  return (
    <details className="rounded-sm border border-rule" open={touched || provider === "aws"}>
      <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2 text-sm">
        <ProviderMark provider={provider} />
        <span className="text-ink">{PROVIDER_LABEL[provider]}</span>
        {costState?.status === "accepted" ? (
          <Tag tone="savings">cost export accepted</Tag>
        ) : costState?.status === "uploading" ? (
          <Tag>uploading and parsing</Tag>
        ) : costState?.status === "rejected" ? (
          <Tag tone="brand">cost export rejected</Tag>
        ) : (
          <span className="flex items-center gap-1 text-xs text-ink-mute">
            <UploadIcon className="h-3 w-3 shrink-0" />
            cost export required to include this provider
          </span>
        )}
      </summary>
      <div className="divide-y divide-rule border-t border-rule">
        {KINDS.map(({ kind, label, hint, required }) => (
          <Slot
            key={kind}
            label={label}
            hint={hint}
            required={required}
            state={slots[slotKey(provider, kind)] ?? { status: "empty" }}
            busy={busy}
            inputId={`upload-${provider}-${kind}`}
            onFile={(file) => onFile(kind, file)}
          />
        ))}
      </div>
    </details>
  );
}

function Slot({
  label,
  hint,
  required,
  state,
  busy,
  inputId,
  onFile,
}: {
  label: string;
  hint: string;
  required?: boolean;
  state: SlotState;
  busy: boolean;
  inputId: string;
  onFile: (file: File) => void;
}) {
  const [over, setOver] = useState(false);
  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        const file = event.dataTransfer.files?.[0];
        if (file && !busy) onFile(file);
      }}
      className={`px-3 py-2.5 transition-colors duration-150 ${over ? "bg-brand-tint" : ""}`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <label htmlFor={inputId} className="flex cursor-pointer items-center gap-1.5 text-sm text-ink">
          <UploadIcon className="h-3.5 w-3.5 shrink-0 text-ink-mute" />
          {label}
          {required ? <span className="text-brand"> *</span> : null}
        </label>
        <input
          id={inputId}
          type="file"
          accept=".json,.csv,.gz,application/json,text/csv,application/gzip"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onFile(file);
            event.target.value = "";
          }}
          className="max-w-[18rem] cursor-pointer text-xs text-ink-soft file:cursor-pointer disabled:cursor-not-allowed disabled:file:cursor-not-allowed"
        />
      </div>
      <p className="mt-0.5 text-xs text-ink-mute">{hint}. Drag a file here or choose one.</p>
      <SlotResult state={state} />
    </div>
  );
}

function UploadIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M12 15V4" />
      <path d="M7.5 8.5 12 4l4.5 4.5" />
      <path d="M4.5 15.5V18a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-2.5" />
    </svg>
  );
}

function SlotResult({ state }: { state: SlotState }) {
  if (state.status === "empty") return null;
  if (state.status === "uploading") {
    return <p className="mt-1 text-xs text-ink-soft">{"Parsing\u2026"}</p>;
  }
  if (state.status === "rejected") {
    return (
      <p className="mt-1 text-xs text-brand">
        <Tag tone="brand">rejected</Tag> <span className="ml-1">{state.detail}</span>
      </p>
    );
  }
  const source = state.report.source;
  return (
    <div className="mt-1 space-y-1 text-xs">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Tag tone="savings">accepted</Tag>
        <span className="num text-ink-soft">{source.detected_format}</span>
        <span className="text-ink-soft">
          {source.raw_rows.toLocaleString()} rows read, {source.stored_records.toLocaleString()}{" "}
          stored
          {source.rejected_rows > 0 ? `, ${source.rejected_rows} rejected` : ""}
        </span>
        {source.period_start && source.period_end ? (
          <span className="num text-ink-soft">
            {source.period_start} to {source.period_end}
          </span>
        ) : null}
        {source.currency ? <span className="num text-ink-soft">{source.currency}</span> : null}
      </p>
      {source.data_through_note ? (
        <p className="text-ink-mute">{source.data_through_note}</p>
      ) : null}
      {state.report.rejections.slice(0, 3).map((rejection) => (
        <p key={`${rejection.row_number}-${rejection.code}`} className="text-ink-mute">
          row {rejection.row_number}: {rejection.detail}
        </p>
      ))}
    </div>
  );
}

function Templates() {
  return (
    <p className="text-xs text-ink-mute">
      Evidence files use four documented shapes. Download a template to fill in:{" "}
      {KINDS.filter(({ kind }) => kind !== "cost").map(({ kind, label }, index) => (
        <span key={kind}>
          {index > 0 ? ", " : ""}
          <a
            href={client.templateUrl(kind)}
            download={`cloudcause-${kind}-template.json`}
            className="underline decoration-rule-strong underline-offset-2 hover:text-brand"
          >
            {label.toLowerCase()}
          </a>
        </span>
      ))}
      . A cost export needs no template: upload the provider's own file.
    </p>
  );
}

function SealedSummary({
  summary,
  onDiscard,
  busy,
}: {
  summary: DatasetSummary;
  onDiscard: () => void;
  busy: boolean;
}) {
  const missing = summary.providers.filter(
    (provider) =>
      !(summary.available_source_types[provider] ?? []).some((type) =>
        ["metric", "audit", "inventory", "recommendation"].includes(type),
      ),
  );
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Tag tone="savings">sealed</Tag>
        <span className="text-ink">
          {summary.total_records.toLocaleString()} normalized rows across{" "}
          {summary.sources.length} source{summary.sources.length === 1 ? "" : "s"}
        </span>
        {summary.period_start && summary.period_end ? (
          <span className="num text-ink-soft">
            {summary.period_start} to {summary.period_end}
          </span>
        ) : null}
        {summary.currency ? <span className="num text-ink-soft">{summary.currency}</span> : null}
      </div>

      <p className="text-xs text-ink-soft">
        The brief below now uses the period found in your data, not the demo dates.
      </p>

      {missing.length > 0 ? (
        <p className="max-w-[80ch] rounded-sm border border-caution/40 bg-caution-tint px-3 py-2 text-xs text-ink">
          {missing.map((provider) => PROVIDER_LABEL[provider]).join(", ")} has cost data only, so
          CloudCause can measure what changed but not confirm why. Findings will be published as an
          unexplained increase. Adding metrics, audit events, inventory, or provider
          recommendations for the same period is what raises them.
        </p>
      ) : null}

      <button
        type="button"
        disabled={busy}
        onClick={onDiscard}
        className="rounded-sm border border-rule-strong bg-surface px-4 py-2 text-sm text-ink-soft transition-colors duration-150 hover:border-brand-edge hover:text-ink disabled:opacity-45"
      >
        Delete and start over
      </button>
    </div>
  );
}
