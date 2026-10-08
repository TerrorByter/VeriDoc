import { useState } from "react";
import { APPLICATION_DOC } from "../../../types";
import type { TimelineKind } from "../../../types";
import type { StageProps } from "../../workspace/StageDetail";
import { Empty, Pill } from "../../ui";

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

export function Timeline({ kase, onView }: StageProps) {
  const [trade, setTrade] = useState<string | null>(null);
  const events = kase.timeline;
  if (events.length === 0) return <Empty title="No dated facts in this case" />;
  const trades = [...new Set(events.map((e) => e.ref).filter(Boolean))];
  const shown = trade === null ? events : events.filter((e) => e.ref === trade);

  return (
    <>
      <p className="muted">Every dated fact in the case, in order. Pick a trade to follow it from order to payment.</p>
      {trades.length > 0 && (
        <div className="chips" role="group" aria-label="Follow one trade">
          {[null, ...trades].map((t) => (
            <button key={t ?? "all"} type="button" className="chip" aria-pressed={trade === t} onClick={() => setTrade(t)}>
              {t ?? "Everything"}
            </button>
          ))}
        </div>
      )}
      <ol className="timeline">
        {shown.map((e, i) => {
          const flagged = e.evidence_ids.length > 0;
          return (
            <li key={`${e.doc_id}-${e.kind}-${e.on}-${i}`} className={flagged ? "flagged" : ""}>
              <time dateTime={e.on} className="mono small">{DATE.format(new Date(`${e.on}T00:00:00`))}</time>
              <div className="tl-body">
                <div className="tl-top">
                  <strong>{KIND_LABEL[e.kind]}</strong>
                  {e.ref && <span className="mono small muted">{e.ref}</span>}
                  {flagged && <Pill tone="high">Date anomaly, {e.evidence_ids.join(", ")}</Pill>}
                </div>
                <p>{e.label}</p>
                {e.doc_id !== APPLICATION_DOC && (
                  <button
                    type="button"
                    className="link mono small"
                    onClick={() => onView({ docId: e.doc_id, page: e.page, evidenceId: e.evidence_ids[0] })}
                  >
                    {e.doc_id}, p.{e.page}
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </>
  );
}
