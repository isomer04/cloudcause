import Link from "next/link";
import { notFound } from "next/navigation";

import { ProgressLedger } from "@/components/progress-ledger";
import { ReportView } from "@/components/report-view";
import { GatewayError, server } from "@/lib/gateway";
import type { ProgressEvent } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function InvestigationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let state;
  try {
    state = await server.investigation(id);
  } catch (error) {
    if (error instanceof GatewayError && error.status === 404) notFound();
    return (
      <div className="mx-auto w-full max-w-6xl px-5 py-10 sm:px-8">
        <p className="rounded-lg border border-brand-edge bg-brand-tint px-4 py-3.5 text-sm text-ink">
          The gateway is not answering: {(error as Error).message}
        </p>
      </div>
    );
  }

  let events: ProgressEvent[] = [];
  try {
    events = await server.progress(id);
  } catch {
    events = [];
  }

  return (
    <div className="mx-auto w-full max-w-6xl px-5 py-7 sm:px-8 lg:py-10">
      <p className="text-xs text-ink-mute">
        <Link href="/history" className="underline decoration-rule-strong underline-offset-2 hover:text-brand">
          History
        </Link>
        <span aria-hidden> / </span>
        <span className="num">{state.investigation_id}</span>
      </p>

      <div className="mt-5 space-y-8">
        {state.report ? (
          <ReportView report={state.report} />
        ) : (
          <div>
            <h1 className="font-display text-[1.75rem] leading-tight text-ink">{state.question}</h1>
            <p className="mt-2 text-sm text-ink-soft">
              This investigation is {state.status}. {state.error ?? state.message}
            </p>
          </div>
        )}

        <ProgressLedger
          investigationId={state.investigation_id}
          status={state.status}
          events={events}
          error={state.error}
        />
      </div>
    </div>
  );
}
