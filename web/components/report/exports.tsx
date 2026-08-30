"use client";

import { useState } from "react";

import { client } from "@/lib/gateway-client";

type BusyAction = "pdf" | "copy" | null;

/**
 * Export is a hand-off, so every control copies what a human will paste into
 * a ticket, attach to a review, or open in another tool: the gateway's own
 * rendering, unaltered. The four controls are not peers - PDF is the primary
 * hand-off artifact, the Markdown pair is secondary, and JSON is a tertiary,
 * engineer-facing escape hatch - so the layout gives them three visual units
 * (PDF button, Markdown segmented pair, JSON link) rather than four
 * same-weight buttons.
 */
export function Exports({ investigationId }: { investigationId: string }) {
  const [note, setNote] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  const busy = busyAction !== null;

  function announce(message: string) {
    setNote(message);
    setTimeout(() => setNote(null), 4000);
  }

  async function copyMarkdown() {
    setBusyAction("copy");
    try {
      const response = await fetch(client.markdownUrl(investigationId));
      if (!response.ok) throw new Error(`gateway returned ${response.status}`);
      await navigator.clipboard.writeText(await response.text());
      announce("Markdown report copied.");
    } catch (error) {
      announce(`Could not copy: ${(error as Error).message}`);
    } finally {
      setBusyAction(null);
    }
  }

  async function downloadPdf() {
    setBusyAction("pdf");
    try {
      const response = await fetch(client.pdfUrl(investigationId));
      if (!response.ok) throw new Error(`gateway returned ${response.status}`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `${investigationId}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      announce(`Could not prepare PDF: ${(error as Error).message}`);
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <section aria-label="Export" className="grid grid-cols-2 gap-2.5">
      <button
        type="button"
        onClick={downloadPdf}
        disabled={busy}
        aria-busy={busyAction === "pdf"}
        className="col-span-2 rounded-sm bg-brand px-3.5 py-2 text-sm font-medium text-on-brand transition-colors duration-150 hover:bg-brand-press disabled:opacity-60"
      >
        {busyAction === "pdf" ? "Preparing PDF…" : "Download PDF"}
      </button>

      <div className="col-span-2 flex overflow-hidden rounded-sm border border-rule-strong">
        <button
          type="button"
          onClick={copyMarkdown}
          disabled={busy}
          aria-busy={busyAction === "copy"}
          className="flex-1 border-r border-rule-strong bg-surface px-3 py-2 text-sm text-ink-soft transition-colors duration-150 hover:bg-sunken hover:text-ink disabled:opacity-60"
        >
          {busyAction === "copy" ? "Copying…" : "Copy Markdown"}
        </button>
        <a
          href={client.markdownUrl(investigationId)}
          download={`${investigationId}.md`}
          aria-disabled={busy}
          className="flex-1 bg-surface px-3 py-2 text-center text-sm text-ink-soft transition-colors duration-150 hover:bg-sunken hover:text-ink"
        >
          Download .md
        </a>
      </div>

      <a
        href={client.jsonUrl(investigationId)}
        target="_blank"
        rel="noreferrer noopener"
        className="col-span-2 inline-flex items-center gap-1 justify-self-start text-xs text-ink-mute underline decoration-rule-strong underline-offset-2 transition-colors duration-150 hover:text-ink"
      >
        Open JSON
        <span aria-hidden>↗</span>
      </a>

      <span aria-live="polite" className="col-span-2 text-xs text-ink-mute">
        {note}
      </span>
    </section>
  );
}
