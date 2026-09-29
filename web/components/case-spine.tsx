const STAGES = [
  ["01", "Source", "#source"],
  ["02", "Brief", "#brief"],
  ["03", "Investigation", "#trail"],
  ["04", "Verdict", "#verdict"],
  ["05", "Evidence", "#evidence"],
] as const;

export function CaseSpine() {
  return (
    <aside className="case-spine" aria-label="Case spine">
      <p className="dossier-label border-b border-rule-strong pb-2">Case spine</p>
      <nav aria-label="Case sections" className="mt-4 flex gap-5 overflow-x-auto pb-2 lg:flex-col lg:gap-4 lg:overflow-visible">
        {STAGES.map(([number, label, href]) => (
          <a key={href} href={href} className="group flex shrink-0 items-center gap-2 hover:text-brand">
            <span className="num text-brand">{number}</span>
            <span>{label}</span>
          </a>
        ))}
      </nav>
      <div className="mt-8 hidden border border-brand-edge bg-brand-tint p-3 text-[0.6875rem] leading-relaxed text-brand lg:block">
        <span className="dossier-label mb-1 block text-brand">Read-only protection</span>
        No CloudCause tool can delete, stop, scale, or modify a cloud resource.
      </div>
    </aside>
  );
}

export function DossierHeading({ number, children }: { number: string; children: React.ReactNode }) {
  return <h2 className="dossier-label mb-5">{number} / {children}</h2>;
}
