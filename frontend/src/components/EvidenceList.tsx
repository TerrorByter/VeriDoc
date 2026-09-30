import { useState } from "react";
import type { AuditEvent, Category, Evidence, FlagDecision } from "../types";

const SEVERITY_RANK: Record<string, number> = { critical: 4, high: 3, medium: 2, low: 1, info: 0 };

const TABS: { key: Category; label: string }[] = [
  { key: "fraud_signal", label: "Fraud signals" },
  { key: "compliance", label: "Compliance" },
  { key: "completeness", label: "Completeness" },
];

interface Props {
  evidence: Evidence[];
  audit: AuditEvent[];
  onDecide: (evidenceId: string, decision: FlagDecision, reason: string) => Promise<void>;
}

function latestDecision(audit: AuditEvent[], evidenceId: string): string | null {
  const events = audit.filter(
    (e) => e.evidence_id === evidenceId && e.action.startsWith("flag_"),
  );
  return events.length ? events[events.length - 1].decision : null;
}

function Card({
  ev,
  decision,
  onDecide,
}: {
  ev: Evidence;
  decision: string | null;
  onDecide: Props["onDecide"];
}) {
  const [reason, setReason] = useState("");
  return (
    <li className={`evidence sev-${ev.severity}`}>
      <div className="ev-head">
        <span className="sev">{ev.severity}</span>
        <strong>{ev.check_id}</strong>
        <span className="agent-chip">{ev.agent}</span>
        <span className="muted">
          {ev.evidence_id} · {ev.document} p.{ev.page} · {ev.method} ·{" "}
          {Math.round(ev.confidence * 100)}% confidence
        </span>
      </div>
      <p>{ev.explanation}</p>
      {(ev.expected || ev.observed) && (
        <dl className="compare">
          {ev.expected && (<><dt>Expected</dt><dd>{ev.expected}</dd></>)}
          {ev.observed && (<><dt>Observed</dt><dd>{ev.observed}</dd></>)}
        </dl>
      )}
      {ev.quote && <blockquote>{ev.quote}</blockquote>}
      <div className="row">
        <input
          type="text"
          value={reason}
          placeholder="Reason (optional)"
          aria-label={`Reason for ${ev.evidence_id}`}
          onChange={(e) => setReason(e.target.value)}
        />
        {(["confirmed", "dismissed"] as const).map((d) => (
          <button
            key={d}
            type="button"
            className={`btn chip${decision === d ? " active" : ""}`}
            aria-pressed={decision === d}
            onClick={() => onDecide(ev.evidence_id, d, reason)}
          >
            {d === "confirmed" ? "Confirm flag" : "Dismiss"}
          </button>
        ))}
      </div>
    </li>
  );
}

export function EvidenceList({ evidence, audit, onDecide }: Props) {
  const [tab, setTab] = useState<Category>("fraud_signal");
  const [agent, setAgent] = useState<string | null>(null);
  const agents = [...new Set(evidence.map((e) => e.agent).filter(Boolean))];
  const shown = evidence
    .filter((e) => e.category === tab && e.severity !== "info")
    .filter((e) => agent === null || e.agent === agent)
    .sort((a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity]);
  const count = (c: Category) =>
    evidence.filter((e) => e.category === c && e.severity !== "info").length;

  return (
    <section className="card" aria-labelledby="evidence-title">
      <h2 id="evidence-title">Findings</h2>
      <div className="tabs" role="tablist">
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={`tab${tab === key ? " active" : ""}`}
            onClick={() => setTab(key)}
          >
            {label} <span className="count">{count(key)}</span>
          </button>
        ))}
      </div>
      {agents.length > 1 && (
        <div className="row filters" role="group" aria-label="Filter by agent">
          {[null, ...agents].map((a) => (
            <button
              key={a ?? "all"}
              type="button"
              className={`btn chip${agent === a ? " active" : ""}`}
              aria-pressed={agent === a}
              onClick={() => setAgent(a)}
            >
              {a ?? "All agents"}
            </button>
          ))}
        </div>
      )}
      {shown.length === 0 ? (
        <p className="muted empty">No findings in this category.</p>
      ) : (
        <ul className="evidence-list">
          {shown.map((ev) => (
            <Card
              key={ev.evidence_id}
              ev={ev}
              decision={latestDecision(audit, ev.evidence_id)}
              onDecide={onDecide}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
