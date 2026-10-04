import { Fragment } from "react";
import { AGENT_TITLE, EVIDENCE_LABEL, LEAD_STATUS_LABEL, PATTERN_LABEL } from "../../../catalogue";
import type { Evidence, Lead, ViewTarget } from "../../../types";
import { Empty, LEAD_STATUS_TONE, Pill } from "../../ui";
import { findingTarget } from "../Findings";

/** The tags that mark a lead as AI output: kind, specialist, the agent's status, pattern and what code established. */
export function LeadTags({ lead }: { lead: Lead }) {
  const evidence = lead.evidence_status && EVIDENCE_LABEL[lead.evidence_status];
  return (
    <>
      <Pill tone="brand">AI lead</Pill>
      <span className="muted small">{lead.kind === "triage" ? "Triage" : "Pattern"}, {AGENT_TITLE[lead.agent]}</span>
      <Pill tone={LEAD_STATUS_TONE[lead.investigation_status]}>
        {LEAD_STATUS_LABEL[lead.kind][lead.investigation_status]}
      </Pill>
      {lead.pattern && <Pill tone="neutral">{PATTERN_LABEL[lead.pattern]}</Pill>}
      {evidence && <span className="muted small" title={evidence.title}>{evidence.label}</span>}
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

interface LeadProps {
  evidence: Evidence[];
  /** Show a cited call in the trace */
  onCite: (callId: string) => void;
  onView: (target: ViewTarget) => void;
}

/** One verified lead: its hypothesis, each test with what was expected and observed and the calls it rests on,
 * what is missing, and the findings it judges. */
function LeadCard({ lead, evidence, onCite, onView }: LeadProps & { lead: Lead }) {
  const openFinding = (id: string) => {
    const ev = evidence.find((e) => e.evidence_id === id);
    const target = ev && findingTarget(ev);
    return target ? () => onView(target) : null;
  };
  return (
    <li className="lead">
      <div className="finding-top">
        <span className="mono muted">{lead.lead_id}</span>
        <LeadTags lead={lead} />
      </div>
      <strong>{lead.hypothesis}</strong>
      <ul className="lead-tests">
        {lead.tests.map((t, i) => (
          <li key={i}>
            {t.expected && <><span className="muted small">Expected</span><span>{t.expected}</span></>}
            <span className="muted small">Observed</span><span>{t.observed}</span>
            <span className="lead-test-refs muted small">
              <Refs label="Rests on" ids={t.cites} open={(id) => () => onCite(id)} />
            </span>
          </li>
        ))}
      </ul>
      {lead.missing_evidence.length > 0 && (
        <p className="small"><span className="muted">Missing evidence:</span> {lead.missing_evidence.join("; ")}</p>
      )}
      {lead.finding_ids.length > 0 && (
        <p className="muted small">
          <Refs label={lead.finding_ids.length === 1 ? "Finding" : "Findings"} ids={lead.finding_ids} open={openFinding} />
        </p>
      )}
    </li>
  );
}

/** Kept leads that need the analyst (supported or unresolved). */
export function KeptLeads({ leads, withheld, clear, ...props }: LeadProps & {
  leads: Lead[];
  /** How many leads the verifier withheld, to tell "none raised" from "none passed" */
  withheld: number;
  /** How many kept checks came back clear */
  clear: number;
}) {
  if (leads.length === 0) {
    const why = withheld ? "No lead passed the verifier."
      : clear ? "Every check the AI team recorded came back clear." : "The AI team raised no leads.";
    return <Empty title="No AI leads">{why}</Empty>;
  }
  return (
    <ul className="leads">
      {leads.map((lead) => <LeadCard key={lead.lead_id} lead={lead} {...props} />)}
    </ul>
  );
}

/** Checks that came back clear: a record of what was looked at, collapsed, never a flag. */
export function ClearChecks({ leads, ...props }: LeadProps & { leads: Lead[] }) {
  return (
    <details className="withheld-leads">
      <summary>Show {leads.length === 1 ? "1 clear check" : `${leads.length} clear checks`}</summary>
      <ul className="leads">
        {leads.map((lead) => <LeadCard key={lead.lead_id} lead={lead} {...props} />)}
      </ul>
    </details>
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
            <strong>{lead.hypothesis}</strong>
            <p className="small">Withheld: {lead.withheld_reason}</p>
          </li>
        ))}
      </ul>
    </details>
  );
}
