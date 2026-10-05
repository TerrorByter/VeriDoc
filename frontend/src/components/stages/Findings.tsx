import { useState } from "react";
import { APPLICATION_DOC, REASON_CODES } from "../../types";
import type { AuditEvent, DocRef, Evidence, ReasonCode, Severity, Signal, ViewTarget } from "../../types";
import type { StageProps } from "../workspace/StageDetail";
import { Icon, LinkedText, Pill, SEVERITY_TONE } from "../ui";

const SEVERITY_RANK: Record<Severity, number> = { critical: 4, high: 3, medium: 2, low: 1, info: 0 };

export const bySeverity = (a: Evidence, b: Evidence) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity];

/** Where the document pane opens a finding; null for application data, which has no page */
export const findingTarget = (ev: Evidence): ViewTarget | null =>
  ev.document === APPLICATION_DOC ? null : { docId: ev.document, page: ev.page, evidenceId: ev.evidence_id };

function latestDecision(audit: AuditEvent[], evidenceId: string): string | null {
  const events = audit.filter((e) => e.evidence_id === evidenceId && e.action.startsWith("flag_"));
  return events.length ? events[events.length - 1].decision : null;
}

function FindingCard({ ev, signal, decision, editable, explained, subjects, onView, onDecideFlag }: {
  ev: Evidence;
  signal: Signal | undefined;
  decision: string | null;
  editable: boolean;
  /** Why code explained this finding, when it did: the card is then a quiet receipt, not an open signal */
  explained?: string;
  subjects: Record<string, DocRef>;
} & Pick<StageProps, "onView" | "onDecideFlag">) {
  const [code, setCode] = useState<ReasonCode | "">("");
  const [note, setNote] = useState("");
  const canDismiss = code !== "" && (code !== "other" || note.trim() !== "");
  const target = findingTarget(ev);

  const actions = editable && ev.category === "fraud_signal" && (
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
  );

  const where = ev.document === APPLICATION_DOC ? "Application data" : `${ev.document}, page ${ev.page}`;
  const detail = (
    <>
      <p className={explained ? "muted" : undefined}>{ev.explanation}</p>
      {(ev.expected || ev.observed) && (
        <dl className="compare">
          {ev.expected && (<><dt>Expected</dt><dd>{ev.expected}</dd></>)}
          {ev.observed && (<><dt>Observed</dt><dd>{ev.observed}</dd></>)}
        </dl>
      )}
      {ev.quote && <blockquote className="quote">{ev.quote}</blockquote>}
      <p className="finding-meta muted">
        {where}. {ev.agent} {ev.method === "llm" ? "model check" : "rule check"}, {Math.round(ev.confidence * 100)}%
        confidence. Rule severity {ev.severity}.
      </p>
    </>
  );
  const showButton = target && (
    <button type="button" className="link" onClick={() => onView(target)}>
      Show in document
    </button>
  );

  if (explained) {
    return (
      <li className="finding explained">
        <div className="finding-top">
          <span className="mono muted">{ev.evidence_id}</span>
          <strong>{signal?.name ?? ev.check_id}</strong>
          {signal && <span className="mono muted small">{signal.signal_id}</span>}
          <Pill tone="low">Explained by code</Pill>
          {decision && <Pill tone={decision === "confirmed" ? "crit" : "neutral"}>{decision}</Pill>}
        </div>
        <p className="explained-why">
          <Icon name="done" size={16} />
          <span>
            <LinkedText text={explained} subjects={subjects} onView={onView} />.{" "}
            <span className="muted">The rule score and route still count this finding; the explanation changes neither.</span>
          </span>
        </p>
        <details className="disclosure review-anyway">
          <summary>Review the rule finding anyway</summary>
          {showButton}
          {detail}
          {actions}
        </details>
      </li>
    );
  }

  return (
    <li className={`finding sev-${ev.severity}`}>
      <div className="finding-top">
        <span className="mono muted">{ev.evidence_id}</span>
        <strong>{signal?.name ?? ev.check_id}</strong>
        {signal && <span className="mono muted small">{signal.signal_id}</span>}
        <Pill tone={SEVERITY_TONE[ev.severity]}>{ev.severity}</Pill>
        {decision && <Pill tone={decision === "confirmed" ? "crit" : "neutral"}>{decision}</Pill>}
        <span className="spacer" />
        {showButton}
      </div>
      {detail}
      {actions}
    </li>
  );
}

/** Findings as cards, most severe first, with the ones code explained after them as quiet receipts. Fraud flags can
 * be confirmed or dismissed while the case is open. */
export function Findings({ evidence, empty, explained = {}, kase, audit, onView, onDecideFlag }: {
  evidence: Evidence[];
  empty: string;
  /** Finding id -> why code explained it */
  explained?: Record<string, string>;
} & StageProps) {
  const editable = kase.allowed_actions.length > 0;
  const card = (ev: Evidence) => (
    <FindingCard
      key={ev.evidence_id}
      ev={ev}
      signal={kase.signals[ev.check_id]}
      decision={latestDecision(audit, ev.evidence_id)}
      editable={editable}
      explained={explained[ev.evidence_id]}
      subjects={kase.subjects}
      onView={onView}
      onDecideFlag={onDecideFlag}
    />
  );
  const open = evidence.filter((e) => !(e.evidence_id in explained)).sort(bySeverity);
  const done = evidence.filter((e) => e.evidence_id in explained).sort(bySeverity);
  return (
    <>
      {open.length > 0 ? <ul className="findings">{open.map(card)}</ul> : done.length === 0 && <p className="muted">{empty}</p>}
      {done.length > 0 && (
        <>
          <h4 className="explained-head">Explained by code <span className="count">{done.length}</span></h4>
          <ul className="findings">{done.map(card)}</ul>
        </>
      )}
    </>
  );
}
