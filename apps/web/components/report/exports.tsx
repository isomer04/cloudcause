"use client";

import { useState } from "react";

import { client } from "@/lib/gateway";

/**
 * Export is a hand-off, so it copies what a human will paste into a ticket or
 * a review: the gateway's own Markdown report, unaltered.
 */
export function Exports({ investigationId }: { investigationId: string }) {
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function copyMarkdown() {
    setBusy(true);
    try {
      const response = await fetch(client.markdownUrl(investigationId));
      if (!response.ok) throw new Error(`gateway returned ${response.status}`);
      await navigator.clipboard.writeText(await response.text());
      setNote("Markdown report copied.");
    } catch (error) {
      setNote(`Could not copy: ${(error as Error).message}`);
    } finally {
      setBusy(false);
      setTimeout(() => setNote(null), 4000);
    }
  }

  return (
    <section aria-label="Export" className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        onClick={copyMarkdown}
        disabled={busy}
        className="rounded-sm border border-rule-strong bg-surface px-3.5 py-2 text-sm text-ink-soft transition-colors duration-150 hover:border-brand-edge hover:text-ink disabled:opacity-50"
      >
        Copy Markdown
      </button>
      <a
        href={client.markdownUrl(investigationId)}
        download={`${investigationId}.md`}
        className="rounded-sm border border-rule-strong bg-surface px-3.5 py-2 text-sm text-ink-soft transition-colors duration-150 hover:border-brand-edge hover:text-ink"
      >
        Download .md
      </a>
      <a
        href={client.jsonUrl(investigationId)}
        target="_blank"
        rel="noreferrer noopener"
        className="rounded-sm border border-rule-strong bg-surface px-3.5 py-2 text-sm text-ink-soft transition-colors duration-150 hover:border-brand-edge hover:text-ink"
      >
        Open JSON
      </a>
      <span aria-live="polite" className="text-xs text-ink-mute">
        {note}
      </span>
    </section>
  );
}
