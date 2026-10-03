import type { AuditEvent } from "../types";

export function AuditTrail({ audit }: { audit: AuditEvent[] }) {
  return (
    <section className="card" aria-labelledby="audit-title">
      <h2 id="audit-title">Audit trail</h2>
      <ol className="audit">
        {audit.map((e, i) => (
          <li key={`${e.timestamp}-${i}`}>
            <time dateTime={e.timestamp}>{new Date(e.timestamp).toLocaleString()}</time>
            <span>
              <strong>{e.action}</strong>
              {e.evidence_id && ` ${e.evidence_id}`}
              {e.decision && ` → ${e.decision}`}
              {e.reason_code && ` (${e.reason_code})`} · {e.actor}
              {e.role && ` as ${e.role}`}
              {e.reason && <span className="muted"> — {e.reason}</span>}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
