import { Fragment } from "react";
import { AGENT_TITLE, PATTERN_LABEL, VERDICT_LABEL } from "../../../catalogue";
import type { Evidence, Lead, ViewTarget } from "../../../types";
import { Empty, Pill, VERDICT_TONE } from "../../ui";
import { findingTarget } from "../Findings";

/** The tags that mark a lead as AI output: kind, specialist, verdict or pattern. */
export function LeadTags({ lead }: { lead: Lead }) {
  return (
    <>
      <Pill tone="brand">AI lead</Pill>
      <span className="muted small">{lead.kind === "triage" ? "Triage" : "Pattern"}, {AGENT_TITLE[lead.agent]}</span>
      {lead.verdict && <Pill tone={VERDICT_TONE[lead.verdict]}>{VERDICT_LABEL[lead.verdict]}</Pill>}
      {lead.pattern && <Pill tone="neutral">{PATTERN_LABEL[lead.pattern]}</Pill>}
    </>
  );
}

/** IDs as links where they lead somewhere, plain text where they do not. */
function Refs({ label, ids, open }: { label: string; ids: string[]; open: (id: string) => (() => void) | null }) {
  return (
    <span>
      {label}{" "}
      {ids.map((id, i) => {
        const go = open(id);
        return (
          <Fragment key={id}>
            {i > 0 && ", "}
            {go ? <button type="button" className="link mono" onClick={go}>{id}</button> : <span className="mono">{id}</span>}
          </Fragment>
        );
      })}
    </span>
  );
}

/** Kept leads, each citing the tool calls it rests on and the findings it is about. */
export function KeptLeads({ leads, evidence, onCite, onView }: {
  leads: Lead[];
  evidence: Evidence[];
  /** Show a cited call in the trace */
  onCite: (callId: string) => void;
  onView: (target: ViewTarget) => void;
}) {
  if (leads.length === 0) return <Empty title="No AI leads">No lead passed the verifier.</Empty>;
  const openFinding = (id: string) => {
    const ev = evidence.find((e) => e.evidence_id === id);
    const target = ev && findingTarget(ev);
    return target ? () => onView(target) : null;
  };
  return (
    <ul className="leads">
      {leads.map((lead) => (
        <li key={lead.lead_id} className="lead">
          <div className="finding-top">
            <span className="mono muted">{lead.lead_id}</span>
            <LeadTags lead={lead} />
          </div>
          <strong>{lead.title}</strong>
          <p>{lead.detail}</p>
          <p className="muted small">
            <Refs label="Rests on" ids={lead.cites} open={(id) => () => onCite(id)} />
            {lead.finding_ids.length > 0 && (
              <> · <Refs label={lead.finding_ids.length === 1 ? "Finding" : "Findings"} ids={lead.finding_ids} open={openFinding} /></>
            )}
          </p>
        </li>
      ))}
    </ul>
  );
}

/** Leads the verifier withheld, with its reason; collapsed, but never hidden. */
export function WithheldLeads({ leads }: { leads: Lead[] }) {
  return (
    <details className="withheld-leads">
      <summary>Show {leads.length === 1 ? "1 withheld lead" : `${leads.length} withheld leads`}</summary>
      <ul className="leads">
        {leads.map((lead) => (
          <li key={lead.lead_id} className="lead withheld">
            <div className="finding-top">
              <span className="mono muted">{lead.lead_id}</span>
              <span className="muted small">{AGENT_TITLE[lead.agent]}</span>
            </div>
            <strong>{lead.title}</strong>
            <p className="small">Withheld: {lead.withheld_reason}</p>
          </li>
        ))}
      </ul>
    </details>
  );
}
