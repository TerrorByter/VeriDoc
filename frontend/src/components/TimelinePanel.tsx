import { useState } from "react";
import { APPLICATION_DOC } from "../types";
import type { TimelineEvent, TimelineKind, ViewTarget } from "../types";

const KIND_LABEL: Record<TimelineKind, string> = {
  incorporated: "Incorporated",
  financial_year_end: "Year end",
  bank_statement: "Statement",
  purchase_order: "Order",
  shipped: "Shipped",
  invoice: "Invoice",
  payment: "Payment",
  application: "Application",
};

const DATE = new Intl.DateTimeFormat("en-SG", { day: "2-digit", month: "short", year: "numeric" });

export function TimelinePanel({
  events,
  onView,
}: {
  events: TimelineEvent[];
  onView: (target: ViewTarget) => void;
}) {
  const [trade, setTrade] = useState<string | null>(null);
  if (events.length === 0) return null;
  const trades = [...new Set(events.map((e) => e.ref).filter(Boolean))];
  const shown = trade === null ? events : events.filter((e) => e.ref === trade);
  const anomalies = events.filter((e) => e.evidence_ids.length > 0).length;

  return (
    <section className="card" aria-labelledby="timeline-title">
      <div className="routing-head">
        <h2 id="timeline-title">Timeline</h2>
        <span className={anomalies ? "status warn" : "status ok"}>
          {anomalies ? `${anomalies} date anomal${anomalies === 1 ? "y" : "ies"}` : "dates consistent"}
        </span>
      </div>
      <p className="muted">
        Every dated fact in the case, in order. Pick a trade to follow it from order to payment.
      </p>
      {trades.length > 0 && (
        <div className="row filters" role="group" aria-label="Follow one trade">
          {[null, ...trades].map((t) => (
            <button
              key={t ?? "all"}
              type="button"
              className={`btn chip${trade === t ? " active" : ""}`}
              aria-pressed={trade === t}
              onClick={() => setTrade(t)}
            >
              {t ?? "Everything"}
            </button>
          ))}
        </div>
      )}
      <ol className="timeline">
        {shown.map((e, i) => {
          const flagged = e.evidence_ids.length > 0;
          const openable = e.doc_id !== APPLICATION_DOC;
          return (
            <li key={`${e.doc_id}-${e.kind}-${e.on}-${i}`} className={`tl-item tl-${e.kind}${flagged ? " flagged" : ""}`}>
              <time dateTime={e.on}>{DATE.format(new Date(`${e.on}T00:00:00`))}</time>
              <div className="tl-body">
                <div className="ev-head">
                  <span className="tl-kind">{KIND_LABEL[e.kind]}</span>
                  {e.ref && <span className="agent-chip">{e.ref}</span>}
                  {flagged && (
                    <span className="tl-flag">⚠ date anomaly · {e.evidence_ids.join(", ")}</span>
                  )}
                </div>
                <p>{e.label}</p>
                {openable && (
                  <button
                    type="button"
                    className="link"
                    onClick={() => onView({ docId: e.doc_id, page: e.page, evidenceId: e.evidence_ids[0] })}
                  >
                    {e.doc_id} p.{e.page} →
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
