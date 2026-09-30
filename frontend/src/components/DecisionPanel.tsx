import { useState } from "react";
import type { AuditEvent, FinalDecision } from "../types";

const OPTIONS: { key: FinalDecision; label: string }[] = [
  { key: "approved", label: "Approve" },
  { key: "more_info_requested", label: "Request more info" },
  { key: "rejected", label: "Reject" },
];

interface Props {
  audit: AuditEvent[];
  onDecide: (decision: FinalDecision, reason: string) => Promise<void>;
}

export function DecisionPanel({ audit, onDecide }: Props) {
  const [reason, setReason] = useState("");
  const recorded = audit.filter((e) => e.action === "final_decision").at(-1);

  return (
    <section className="card" aria-labelledby="decision-title">
      <h2 id="decision-title">Officer decision</h2>
      <p className="muted">
        This system never approves or rejects a loan. The decision is yours and is
        recorded in the audit log.
      </p>
      <textarea
        value={reason}
        rows={3}
        placeholder="Reason (required)"
        aria-label="Reason for decision"
        onChange={(e) => setReason(e.target.value)}
      />
      <div className="row">
        {OPTIONS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            className={`btn ${key === "rejected" ? "danger" : "primary"}`}
            disabled={!reason.trim()}
            onClick={() => onDecide(key, reason.trim())}
          >
            {label}
          </button>
        ))}
      </div>
      {recorded && (
        <p className="recorded" role="status">
          Recorded: <strong>{recorded.decision}</strong> — {recorded.reason}
        </p>
      )}
    </section>
  );
}
