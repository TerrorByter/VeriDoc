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
      {lead.check_reason && (
        <p className="small"><span className="muted">Code check:</span> {lead.check_reason}</p>
      )}
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

/** Flags: kept leads code verified or corroborated. */
export function KeptLeads({ leads, withheld, unchecked, ...props }: LeadProps & {
  leads: Lead[];
  /** How many leads the verifier withheld, to tell "none raised" from "none passed" */
  withheld: number;
  /** How many kept leads code did not verify (observations and clear checks) */
  unchecked: number;
}) {
  if (leads.length === 0) {
    const why = unchecked ? "No lead was verified by code; what the AI team recorded is below."
      : withheld ? "No lead passed the verifier." : "The AI team raised no leads.";
    return <Empty title="No AI leads">{why}</Empty>;
  }
  return (
    <ul className="leads">
      {leads.map((lead) => <LeadCard key={lead.lead_id} lead={lead} {...props} />)}
    </ul>
  );
}

/** Leads that are not flags (observations code could not check, or checks that came back clear): collapsed,
 * never hidden. */
export function CollapsedLeads({ noun, leads, ...props }: LeadProps & { noun: string; leads: Lead[] }) {
  return (
    <details className="withheld-leads">
      <summary>Show {leads.length === 1 ? `1 ${noun}` : `${leads.length} ${noun}s`}</summary>
      <ul className="leads">
        {leads.map((lead) => <LeadCard key={lead.lead_id} lead={lead} {...props} />)}
      </ul>
    </details>
  );
}

/** Rule findings an AI lead confirmed: the rule determined the risk, the AI only agreed, so no second flag. */
export function RuleConfirmations({ leads, evidence }: { leads: Lead[]; evidence: Evidence[] }) {
  return (
    <ul className="reasons muted small">
      {leads.map((lead) => {
        const id = lead.check_facts.rule_finding;
        const check = evidence.find((e) => e.evidence_id === id)?.check_id ?? "a rule finding";
        return <li key={lead.lead_id}>{lead.lead_id}: AI confirmed existing rule finding {check} ({id})</li>;
      })}
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
            <strong>{lead.hypothesis}</strong>
            <p className="small">Withheld: {lead.withheld_reason}</p>
          </li>
        ))}
      </ul>
    </details>
  );
}
