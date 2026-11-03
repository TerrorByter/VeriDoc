import { useState } from "react";
import { APPLICATION_DOC, REASON_CODES } from "../../types";
import type { AuditEvent, DocRef, Evidence, EvidenceRow, ReasonCode, Severity, Signal, ViewTarget } from "../../types";
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

/** "page 2" or "pages 1 to 3" */
const pages = (all: number[]) => {
  const low = Math.min(...all);
  const high = Math.max(...all);
  return low === high ? `page ${low}` : `pages ${low} to ${high}`;
};

/** The figures behind one finding, each with its own page to show it on. The quote is what the button highlights. */
function FindingRows({ rows, target, onView }: {
  rows: EvidenceRow[];
  target: ViewTarget | null;
} & Pick<StageProps, "onView">) {
  const ratios = rows.some((r) => r.ratio);
  return (
    <div className="table-wrap finding-rows">
      <table className="data">
        <thead>
          <tr>
            <th scope="col">Figure</th>
            <th scope="col" className="num">Expected</th>
            <th scope="col" className="num">Observed</th>
            {ratios && <th scope="col" className="num">Ratio</th>}
            <th scope="col"><span className="sr-only">Where</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.label}>
              <th scope="row">{r.label}</th>
              <td className="num mono">{r.expected}</td>
              <td className="num mono">{r.observed}</td>
              {ratios && <td className="num mono">{r.ratio}</td>}
              <td>
                {target ? (
                  <button
                    type="button"
                    className="link"
                    title={r.quote ?? undefined}
                    aria-label={`Show ${r.label} on page ${r.page}`}
                    onClick={() => onView({ ...target, page: r.page, row: i })}
                  >
                    Page {r.page}
                  </button>
                ) : (
                  <span className="muted">Page {r.page}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
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

  const rows = ev.rows ?? [];
  const where = ev.document === APPLICATION_DOC
    ? "Application data"
    : rows.length ? `${ev.document}, ${pages(rows.map((r) => r.page))}` : `${ev.document}, page ${ev.page}`;
  const detail = (
    <>
      <p className={explained ? "muted" : undefined}>{ev.explanation}</p>
      {rows.length > 0 ? (
        <FindingRows rows={rows} target={target} onView={onView} />
      ) : (
        <>
          {(ev.expected || ev.observed) && (
            <dl className="compare">
              {ev.expected && (<><dt>Expected</dt><dd>{ev.expected}</dd></>)}
              {ev.observed && (<><dt>Observed</dt><dd>{ev.observed}</dd></>)}
            </dl>
          )}
          {ev.quote && <blockquote className="quote">{ev.quote}</blockquote>}
        </>
      )}
      {signal && (
        <p className="finding-strength">
          {signal.strength === "definite" ? (
            <><strong>Definite.</strong> A fact on the documents, not a pattern; its cause still needs explaining.</>
          ) : (
            <><strong>Indicative.</strong> Innocent reasons to rule out first: {signal.innocent}.</>
          )}
        </p>
      )}
      <p className="finding-meta muted">
        {where}. {ev.rule_group} {ev.method === "llm" ? "model check" : "rule check"}. Rule severity {ev.severity}.
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
