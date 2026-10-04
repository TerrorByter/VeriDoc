import { DOC_TYPE_LABEL } from "../../catalogue";
import type { ClaimRef } from "../../types";
import type { StageProps } from "../workspace/StageDetail";
import { Section } from "./common";

export function ExtractStage({ kase, onView }: StageProps) {
  // Key figures read from the trade documents, as the reconciliation used them
  const seen = new Set<string>();
  const figures: ClaimRef[] = [];
  for (const chain of kase.chains) {
    for (const step of chain.steps) {
      for (const claim of [step.invoice_claim, step.counterpart]) {
        const key = claim && `${claim.doc_id}|${claim.label}`;
        if (claim && key && !seen.has(key)) {
          seen.add(key);
          figures.push(claim);
        }
      }
    }
  }
  const types = Object.entries(
    kase.docs.reduce<Record<string, number>>((n, d) => ({ ...n, [d.doc_type]: (n[d.doc_type] ?? 0) + 1 }), {}),
  );

  return (
    <>
      <div className="callout">
        {kase.extraction_mode === "llm" ? (
          <p>Claude on Amazon Bedrock read each document. Every quote it returned was checked against the page text; quotes not found were discarded.</p>
        ) : (
          <p>
            The rule-based parser read each document, so no model was called. Switch to the model with{" "}
            <code>EXTRACTION_MODE=llm</code>; the checks after this stage are the same either way.
          </p>
        )}
      </div>

      <Section title="Key figures read" aside="As used to reconcile each trade">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr><th scope="col">Figure</th><th scope="col">Value</th><th scope="col">Source</th></tr>
            </thead>
            <tbody>
              {figures.map((c) => (
                <tr key={`${c.doc_id}-${c.label}`}>
                  <td>{c.label}</td>
                  <td className="mono">{c.value}</td>
                  <td>
                    <button type="button" className="link mono" onClick={() => onView({ docId: c.doc_id, page: c.page })}>
                      {c.doc_id}, p.{c.page}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Documents by type">
        <ul className="type-list">
          {types.map(([type, n]) => (
            <li key={type}><span>{DOC_TYPE_LABEL[type] ?? type}</span><span className="mono">{n}</span></li>
          ))}
        </ul>
      </Section>
    </>
  );
}
