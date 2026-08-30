import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto w-full max-w-3xl px-5 py-16 sm:px-8">
      <h1 className="font-display text-[1.625rem] leading-tight text-ink">
        That investigation is not here
      </h1>
      <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-ink-soft">
        The gateway has no record of it. On the default in-memory history backend, investigations
        are dropped when the gateway restarts or when older runs are pruned.
      </p>
      <p className="mt-5 flex flex-wrap gap-3 text-sm">
        <Link
          href="/"
          className="rounded-sm bg-brand px-3.5 py-2 text-on-brand transition-colors duration-150 hover:bg-brand-press"
        >
          Open a new investigation
        </Link>
        <Link
          href="/history"
          className="rounded-sm border border-rule-strong bg-surface px-3.5 py-2 text-ink-soft transition-colors duration-150 hover:border-brand-edge hover:text-ink"
        >
          See history
        </Link>
      </p>
    </div>
  );
}
