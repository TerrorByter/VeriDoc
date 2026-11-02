import type { ChainStatus, ChainStep, ClaimRef } from "../../../types";
import type { StageProps } from "../../workspace/StageDetail";
import { Empty, Icon, Pill } from "../../ui";

const STEP_LABEL: Record<ChainStep["step"], string> = {
  purchase_order: "Purchase order",
  bill_of_lading: "Shipment (bill of lading or delivery order)",
  payment: "Bank payment",
};

const STATUS: Record<ChainStatus, { label: string; icon: "done" | "flag" | "off" }> = {
  matched: { label: "Matches", icon: "done" },
  contradicts: { label: "Contradicts", icon: "flag" },
  missing: { label: "Missing", icon: "flag" },
  not_provided: { label: "Not provided", icon: "off" },
};

function Source({ claim, onView }: { claim: ClaimRef } & Pick<StageProps, "onView">) {
  return (
    <button type="button" className="link mono small" onClick={() => onView({ docId: claim.doc_id, page: claim.page })}>
      {claim.doc_id}, p.{claim.page}
    </button>
  );
}

export function Chains({ kase, onView }: StageProps) {
  if (kase.chains.length === 0) return <Empty title="No invoices to trace" />;
  return (
    <>
      <p className="muted">
        Each invoice traced through its order, shipment and payment. A break shows both documents&apos;
        figures and the finding behind it.
      </p>
      {kase.chains.map((chain) => (
        <section key={chain.invoice_number} className={`chain${chain.broken ? " broken" : ""}`}>
          <header className="chain-head">
            <h3 className="mono">{chain.invoice_number}</h3>
            <Pill tone={chain.broken ? "high" : "low"}>{chain.broken ? "Chain breaks" : "Reconciles"}</Pill>
          </header>
          <ol className="chain-steps">
            {chain.steps.map((step) => {
              const s = STATUS[step.status];
              return (
                <li key={step.step} className={`chain-step ${step.status}`}>
                  <div className="chain-step-head">
                    <Icon name={s.icon} />
                    <strong>{STEP_LABEL[step.step]}</strong>
                    <span className="muted small">{s.label}</span>
                    {step.evidence_ids.length > 0 && <span className="mono small muted">{step.evidence_ids.join(", ")}</span>}
                  </div>
                  {step.status !== "not_provided" && (
                    <dl className="compare">
                      <dt>{step.invoice_claim.label}</dt>
                      <dd><span className="mono">{step.invoice_claim.value}</span> <Source claim={step.invoice_claim} onView={onView} /></dd>
                      {step.counterpart && (
                        <>
                          <dt>{step.counterpart.label}</dt>
                          <dd><span className="mono">{step.counterpart.value}</span> <Source claim={step.counterpart} onView={onView} /></dd>
                        </>
                      )}
                    </dl>
                  )}
                  {step.note && <p className="muted small">{step.note}</p>}
                </li>
              );
            })}
          </ol>
        </section>
      ))}
    </>
  );
}
