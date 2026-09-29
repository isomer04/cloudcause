import { Tag } from "@/components/marks";
import { stamp } from "@/lib/format";
import type { Evidence } from "@/lib/types";

/**
 * Every row here is one verifiable observation with an ID the report cites.
 * Statements that came from resource names, tags, or audit text are marked:
 * they are data, and the reader should not treat them as instructions.
 */
export function EvidenceTable({ evidence }: { evidence: Evidence[] }) {
  if (evidence.length === 0) {
    return <p className="text-sm text-ink-mute">No evidence was attached to this finding.</p>;
  }

  return (
    <div className="overflow-x-auto border border-rule">
      <table className="hairline-table">
        <thead className="bg-sunken">
          <tr>
            <th scope="col">Evidence</th>
            <th scope="col">Source</th>
            <th scope="col">Observed</th>
            <th scope="col">Statement</th>
          </tr>
        </thead>
        <tbody>
          {evidence.map((item) => (
            <tr key={item.evidence_id}>
              <td className="num whitespace-nowrap text-ink-soft">{item.evidence_id}</td>
              <td className="num max-w-[16rem] wrap-break-word text-ink-soft">
                {item.source_type}:{item.source_id}
              </td>
              <td className="num whitespace-nowrap text-ink-mute">{stamp(item.observed_at)}</td>
              <td className="min-w-88 leading-relaxed">
                {item.statement}
                {item.contains_untrusted_text ? (
                  <span className="ml-2 align-middle">
                    <Tag tone="caution">untrusted text</Tag>
                  </span>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
