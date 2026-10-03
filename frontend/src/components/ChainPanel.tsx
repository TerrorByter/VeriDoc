import type { ChainStatus, ChainStep, TransactionChain } from "../types";

const STEP_LABEL: Record<ChainStep["step"], string> = {
  purchase_order: "Purchase order",
  bill_of_lading: "Bill of lading",
  payment: "Bank payment",
};

const STATUS_MARK: Record<ChainStatus, { mark: string; label: string }> = {
  matched: { mark: "✓", label: "matches" },
  contradicts: { mark: "✗", label: "contradicts" },
  missing: { mark: "✗", label: "missing" },
  not_provided: { mark: "–", label: "not provided" },
};

function Step({ step }: { step: ChainStep }) {
  const { mark, label } = STATUS_MARK[step.status];
  return (
    <li className={`chain-step chain-${step.status}`}>
      <div className="chain-step-head">
        <span className="chain-mark" aria-hidden="true">{mark}</span>
        <strong>{STEP_LABEL[step.step]}</strong>
        <span className="muted">{label}</span>
        {step.evidence_ids.length > 0 && (
          <span className="muted"> · {step.evidence_ids.join(", ")}</span>
        )}
      </div>
      {step.status !== "not_provided" && (
        <dl className="compare">
          <dt>{step.invoice_claim.label}</dt>
          <dd>
            {step.invoice_claim.value}
            <span className="muted"> ({step.invoice_claim.doc_id} p.{step.invoice_claim.page})</span>
          </dd>
          {step.counterpart && (
            <>
              <dt>{step.counterpart.label}</dt>
              <dd>
                {step.counterpart.value}
                <span className="muted"> ({step.counterpart.doc_id} p.{step.counterpart.page})</span>
              </dd>
            </>
          )}
        </dl>
      )}
      {step.note && <p className="muted chain-note">{step.note}</p>}
    </li>
  );
}

export function ChainPanel({ chains }: { chains: TransactionChain[] }) {
  if (chains.length === 0) return null;
  return (
    <section className="card" aria-labelledby="chain-title">
      <h2 id="chain-title">Transaction chains</h2>
      <p className="muted">
        Each invoice traced through its order, shipment and payment. A break shows
        both documents' figures and the finding behind it.
      </p>
      {chains.map((chain) => (
        <div key={chain.invoice_number} className={`chain${chain.broken ? " broken" : ""}`}>
          <h3>
            {chain.invoice_number}{" "}
            <span className={`status ${chain.broken ? "warn" : "ok"}`}>
              {chain.broken ? "chain breaks" : "reconciles"}
            </span>
          </h3>
          <ol className="chain-steps">
            {chain.steps.map((step) => (
              <Step key={step.step} step={step} />
            ))}
          </ol>
        </div>
      ))}
    </section>
  );
}
