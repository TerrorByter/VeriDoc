import { usd } from "../../catalogue";
import { href } from "../../route";
import type { CaseView } from "../../types";
import { BAND_TONE, PathPill, Pill, StatusPill } from "../ui";

export function CaseHeader({ kase, onDecide }: { kase: CaseView; onDecide: () => void }) {
  const canDecide = kase.allowed_actions.length > 0;
  return (
    <header className="case-header">
      <div className="case-title">
        <a className="back muted" href={href({ screen: "cases" })}>Past cases</a>
        <h1 className="mono case-id">{kase.case_id}</h1>
        <p className="muted">
          {kase.company_name ?? "Applicant not stated"}, {kase.docs.length} documents
        </p>
      </div>
      <dl className="case-stats">
        {kase.routing && (
          <div>
            <dt>Route</dt>
            <dd><PathPill path={kase.routing.path} />{kase.routing.edd_required && <Pill tone="crit">EDD</Pill>}</dd>
          </div>
        )}
        <div>
          <dt>Fraud risk</dt>
          <dd>
            <Pill tone={BAND_TONE[kase.risk.band]}>{kase.risk.band}</Pill>
            <span className="score">{kase.risk.score}<span className="muted">/100</span></span>
          </dd>
        </div>
        {kase.readiness && (
          <div>
            <dt>Readiness</dt>
            <dd><Pill tone={kase.readiness.status === "Complete" ? "low" : "med"}>{kase.readiness.status}</Pill></dd>
          </div>
        )}
        <div>
          <dt>Status</dt>
          <dd><StatusPill status={kase.status} /></dd>
        </div>
        <div>
          <dt>AWS cost</dt>
          {/* No cost record: the case called no AWS service. usd null: a model is unpriced */}
          <dd className="mono">{kase.cost ? usd(kase.cost.usd) : usd("0")}</dd>
        </div>
      </dl>
      <button type="button" className="btn primary" onClick={onDecide} disabled={!canDecide}
        title={canDecide ? undefined : "The case is closed"}>
        {kase.status === "escalated" ? "Record MLRO decision" : "Record decision"}
      </button>
    </header>
  );
}
