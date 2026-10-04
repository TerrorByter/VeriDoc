import { useState } from "react";
import { APPLICATION_DOC, REASON_CODES } from "../../types";
import type { AuditEvent, Evidence, ReasonCode, Severity, Signal, ViewTarget } from "../../types";
import type { StageProps } from "../workspace/StageDetail";
import { Empty, Pill, SEVERITY_TONE } from "../ui";

const SEVERITY_RANK: Record<Severity, number> = { critical: 4, high: 3, medium: 2, low: 1, info: 0 };

export const bySeverity = (a: Evidence, b: Evidence) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity];

/** Where the document pane opens a finding; null for application data, which has no page */
export const findingTarget = (ev: Evidence): ViewTarget | null =>
  ev.document === APPLICATION_DOC ? null : { docId: ev.document, page: ev.page, evidenceId: ev.evidence_id };

function latestDecision(audit: AuditEvent[], evidenceId: string): string | null {
  const events = audit.filter((e) => e.evidence_id === evidenceId && e.action.startsWith("flag_"));
  return events.length ? events[events.length - 1].decision : null;
}

function FindingCard({ ev, signal, decision, editable, onView, onDecideFlag }: {
  ev: Evidence;
  signal: Signal | undefined;
  decision: string | null;
  editable: boolean;
} & Pick<StageProps, "onView" | "onDecideFlag">) {
  const [code, setCode] = useState<ReasonCode | "">("");
  const [note, setNote] = useState("");
  const canDismiss = code !== "" && (code !== "other" || note.trim() !== "");
  const where = ev.document === APPLICATION_DOC ? "Application data" : `${ev.document}, page ${ev.page}`;
  const target = findingTarget(ev);

  return (
    <li className={`finding sev-${ev.severity}`}>
      <div className="finding-top">
        <span className="mono muted">{ev.evidence_id}</span>
        <strong>{signal?.name ?? ev.check_id}</strong>
        {signal && <span className="mono muted small">{signal.signal_id}</span>}
        <Pill tone={SEVERITY_TONE[ev.severity]}>{ev.severity}</Pill>
        {decision && <Pill tone={decision === "confirmed" ? "crit" : "neutral"}>{decision}</Pill>}
        <span className="spacer" />
        {target && (
          <button type="button" className="link" onClick={() => onView(target)}>
            Show in document
          </button>
        )}
      </div>
      <p>{ev.explanation}</p>
      {(ev.expected || ev.observed) && (
        <dl className="compare">
          {ev.expected && (<><dt>Expected</dt><dd>{ev.expected}</dd></>)}
          {ev.observed && (<><dt>Observed</dt><dd>{ev.observed}</dd></>)}
        </dl>
      )}
      {ev.quote && <blockquote className="quote">{ev.quote}</blockquote>}
      <p className="finding-meta muted">
        {where}. {ev.agent} agent, {ev.method === "llm" ? "model" : "rule"}, {Math.round(ev.confidence * 100)}% confidence.
      </p>
      {editable && ev.category === "fraud_signal" && (
        <div className="flag-actions">
          <button
            type="button"
            className={`btn small${decision === "confirmed" ? " active" : ""}`}
            aria-pressed={decision === "confirmed"}
            onClick={() => onDecideFlag(ev.evidence_id, "confirmed", null, "")}
          >
            Confirm flag
          </button>
          <select
            id={`dismiss-${ev.evidence_id}`}
            value={code}
            aria-label={`Reason to dismiss ${ev.evidence_id}`}
            onChange={(e) => setCode(e.target.value as ReasonCode | "")}
          >
            <option value="">Dismiss because…</option>
            {Object.entries(REASON_CODES).map(([key, label]) => (
              <option key={key} value={key}>{label}</option>
            ))}
          </select>
          {code !== "" && (
            <input
              id={`note-${ev.evidence_id}`}
              type="text"
              value={note}
              placeholder={code === "other" ? "Note (required)" : "Note (optional)"}
              aria-label={`Note for ${ev.evidence_id}`}
              onChange={(e) => setNote(e.target.value)}
            />
          )}
          <button
            type="button"
            className={`btn small${decision === "dismissed" ? " active" : ""}`}
            aria-pressed={decision === "dismissed"}
            disabled={!canDismiss}
            onClick={() => code && onDecideFlag(ev.evidence_id, "dismissed", code, note.trim())}
          >
            Dismiss
          </button>
        </div>
      )}
    </li>
  );
}

/** Findings as cards, most severe first. Fraud flags can be confirmed or dismissed while the case is open. */
export function Findings({ evidence, empty, kase, audit, onView, onDecideFlag }: {
  evidence: Evidence[];
  empty: string;
} & StageProps) {
  if (evidence.length === 0) return <Empty title={empty} />;
  const editable = kase.allowed_actions.length > 0;
  return (
    <ul className="findings">
      {[...evidence].sort(bySeverity).map((ev) => (
        <FindingCard
          key={ev.evidence_id}
          ev={ev}
          signal={kase.signals[ev.check_id]}
          decision={latestDecision(audit, ev.evidence_id)}
          editable={editable}
          onView={onView}
          onDecideFlag={onDecideFlag}
        />
      ))}
    </ul>
  );
}
