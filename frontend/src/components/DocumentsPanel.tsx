import type { DocMeta } from "../types";

const LABELS: Record<string, string> = {
  invoice: "Invoice",
  bank_statement: "Bank statement",
  financial_statements: "Financial statements",
  purchase_order: "Purchase order",
  fatca_crs_entity: "FATCA/CRS (entity)",
  fatca_crs_individual: "FATCA/CRS (individual)",
  unknown: "Unrecognised",
};

export function DocumentsPanel({ docs }: { docs: DocMeta[] }) {
  return (
    <section className="card" aria-labelledby="docs-title">
      <h2 id="docs-title">Documents</h2>
      <ul className="doclist">
        {docs.map((d) => (
          <li key={d.doc_id} className={d.doc_type === "unknown" ? "unknown" : ""}>
            <span>{d.filename}</span>
            <span className="muted">{LABELS[d.doc_type] ?? d.doc_type}</span>
            <span className="muted">{d.page_count} p.</span>
            <code title={d.sha256}>{d.sha256.slice(0, 8)}</code>
          </li>
        ))}
      </ul>
    </section>
  );
}
