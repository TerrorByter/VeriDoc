import { useState } from "react";
import { navigate } from "../../../route";
import type { CaseCategory, Evidence, Lead } from "../../../types";
import type { StageProps } from "../../workspace/StageDetail";
import { Pill } from "../../ui";
import { Section } from "../common";
import { bySeverity, findingTarget } from "../Findings";

export type SummaryJump = "queue" | "chains" | "timeline";

/** Items shown per category before "Show all" */
const FIRST = 3;

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** One finding behind a category: the signal's name, what it found, and where. */
function FindingLine({ ev, kase, onView }: { ev: Evidence } & Pick<StageProps, "kase" | "onView">) {
  const target = findingTarget(ev);
  return (
    <li className="summary-item">
      <div className="summary-item-top">
        <span className="mono muted">{ev.evidence_id}</span>
        <strong>{kase.signals[ev.check_id]?.name ?? ev.check_id}</strong>
        <span className="spacer" />
        {target && (
          <button type="button" className="link" onClick={() => onView(target)}>Show in document</button>
        )}
      </div>
      <p className="muted small">{ev.explanation}</p>
    </li>
  );
}

/** One verified AI-team lead behind a category; its trace is in the Agents stage. */
function LeadLine({ lead, caseId }: { lead: Lead; caseId: string }) {
  return (
    <li className="summary-item">
      <div className="summary-item-top">
        <span className="mono muted">{lead.lead_id}</span>
        <strong>{lead.hypothesis}</strong>
        <span className="spacer" />
        <button type="button" className="link" onClick={() => navigate({ screen: "case", caseId, stage: "ai_team" })}>
          Show the AI team's trace
        </button>
      </div>
      {lead.check_reason && <p className="muted small">Code check: {lead.check_reason}</p>}
    </li>
  );
}

function CategoryCard({ category, kase, onView }: { category: CaseCategory } & Pick<StageProps, "kase" | "onView">) {
  const ai = category.source === "ai_team";
  const findings = ai ? [] : kase.evidence.filter((e) => category.ids.includes(e.evidence_id)).sort(bySeverity);
  const leads = ai ? (kase.ai_team?.leads ?? []).filter((l) => category.ids.includes(l.lead_id)) : [];
  const [all, setAll] = useState(false);
  const total = findings.length + leads.length;
  const shownFindings = all ? findings : findings.slice(0, FIRST);
  const shownLeads = all ? leads : leads.slice(0, Math.max(0, FIRST - shownFindings.length));
  return (
    <li className="summary-card">
      <div className="summary-card-head">
        <h4>{category.label}</h4>
        {ai ? <Pill tone="brand">AI team, verified by code</Pill> : <Pill tone="neutral">Rule checks</Pill>}
        <span className="spacer" />
        <span className="muted small">{plural(category.ids.length, ai ? "lead" : "finding")}</span>
      </div>
      <ul className="summary-items">
        {shownFindings.map((ev) => <FindingLine key={ev.evidence_id} ev={ev} kase={kase} onView={onView} />)}
        {shownLeads.map((lead) => <LeadLine key={lead.lead_id} lead={lead} caseId={kase.case_id} />)}
      </ul>
      {total > FIRST && (
        <button type="button" className="btn ghost small summary-more" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${total}`}
        </button>
      )}
    </li>
  );
}

/** The case at a glance: what kind of problem it shows (set by code), and where to go next. */
export function Summary({ kase, onView, onJump }: Pick<StageProps, "kase" | "onView"> & {
  onJump: (view: SummaryJump) => void;
}) {
  const categories = kase.categories ?? [];
  const broken = kase.chains.filter((c) => c.broken).length;
  const anomalies = kase.timeline.filter((e) => e.evidence_ids.length > 0).length;
  const next: { view: SummaryJump; label: string; count: number }[] = [
    { view: "queue", label: plural(kase.investigation.length, "task") + " to resolve", count: kase.investigation.length },
    { view: "chains", label: plural(broken, "broken transaction chain"), count: broken },
    { view: "timeline", label: `${anomalies} timeline ${anomalies === 1 ? "anomaly" : "anomalies"}`, count: anomalies },
  ];

  return (
    <div className="summary">
      <Section title="What the case shows" aside="Set by code from the findings and verified leads" primary>
        {categories.length === 0 ? (
          <div className="summary-none">
            <strong>No issues found</strong>
            <p className="muted">
              No rule check raised a finding and the AI team raised no lead that code verified. A kind of fraud
              no check looks for would not show here, so the documents still need a person's review.
            </p>
          </div>
        ) : (
          <ul className="summary-cards">
            {categories.map((c) => (
              <CategoryCard key={`${c.source}-${c.label}`} category={c} kase={kase} onView={onView} />
            ))}
          </ul>
        )}
      </Section>

      <Section title="Where to look next">
        <ul className="summary-next">
          {next.map((n) => (
            <li key={n.view}>
              <button type="button" className="link" disabled={n.count === 0} onClick={() => onJump(n.view)}>
                {n.label}
              </button>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
