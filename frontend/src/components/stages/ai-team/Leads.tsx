import { Fragment } from "react";
import { AGENT_TITLE, EVIDENCE_LABEL, FACT_LABEL, LEAD_STATUS_LABEL, PATTERN_LABEL } from "../../../catalogue";
import type { DocRef, Evidence, EvidenceStatus, Lead, ViewTarget } from "../../../types";
import { Icon, LEAD_STATUS_TONE, Pill, type IconName } from "../../ui";
import { findingTarget } from "../Findings";

/** The tags that mark a lead as AI output: kind, specialist, the agent's own status and the pattern. What code
 * established is not here: it has its own column on the card. */
export function LeadTags({ lead }: { lead: Lead }) {
  return (
    <>
      <Pill tone="neutral">AI lead</Pill>
      <span className="muted small">{lead.kind === "triage" ? "Triage" : "Pattern"}</span>
      <Pill tone={LEAD_STATUS_TONE[lead.investigation_status]} title="What the agent concluded: not checked by this tag">
        Agent: {LEAD_STATUS_LABEL[lead.kind][lead.investigation_status].toLowerCase()}
      </Pill>
      {lead.pattern && <Pill tone="neutral">{PATTERN_LABEL[lead.pattern]}</Pill>}
    </>
  );
}

const MARK_ICON: Record<EvidenceStatus, IconName> = {
  verified: "done", corroborated: "done", unverified: "off", contradicted: "close",
};

/** What code established about a lead: an icon and words, never colour alone. Where the agent itself concluded
 * the pattern does not hold and code agrees, that is agreement, not an alarm. */
export function StateMark({ status, agent }: { status: EvidenceStatus; agent: Lead["investigation_status"] }) {
  const agrees = status === "contradicted" && agent === "refuted";
  const { label, title } = agrees
    ? { label: "Code agrees: it does not hold", title: "The agent found no such pattern, and a code check of the case data confirms it" }
    : EVIDENCE_LABEL[status];
  return (
    <span className={`state-mark ${agrees ? "agrees" : status}`} title={title}>
      <Icon name={agrees ? "done" : MARK_ICON[status]} size={16} />
      {label}
    </span>
  );
}

const factLabel = (key: string) => FACT_LABEL[key] ?? key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

/** Facts that are ids, amounts and dates are set in mono; names stay in the reading face. */
const MONO_FACTS = new Set(["invoice", "transaction", "amount", "date", "register_uen", "purchase_order", "shipped",
  "payment", "incorporated", "rule_finding", "corroborated_by"]);

const factValue = (key: string, value: string) =>
  key === "amount" && value.trim() !== "" && !Number.isNaN(Number(value))
    ? `SGD ${Number(value).toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : value;

/** The facts a code check read, as a ruled ledger. */
function Ledger({ facts }: { facts: Record<string, string> }) {
  return (
    <dl className="ledger">
      {Object.entries(facts).map(([key, value]) => (
        <Fragment key={key}>
          <dt>{factLabel(key)}</dt>
          <dd className={MONO_FACTS.has(key) ? "mono" : undefined}>{factValue(key, value)}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

/** The document pages a lead's subject is on, one button each. */
function SubjectLinks({ lead, subjects, onView }: {
  lead: Lead;
  subjects: Record<string, DocRef>;
  onView: (target: ViewTarget) => void;
}) {
  const ids = [lead.invoice_number, ...lead.transaction_ids, lead.counterparty]
    .filter((id): id is string => id != null && id in subjects);
  if (ids.length === 0) return null;
  return (
    <div className="subject-links">
      {[...new Set(ids)].map((id) => (
        <button key={id} type="button" className="btn small"
          onClick={() => onView({ docId: subjects[id].doc_id, page: subjects[id].page })}>
          <Icon name="doc" size={14} /> Show {id} in document
        </button>
      ))}
    </div>
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
  /** Bank rows, invoices and counterparties -> the page they are on */
  subjects: Record<string, DocRef>;
  /** Show a cited call in the trace */
  onCite: (callId: string) => void;
  onView: (target: ViewTarget) => void;
}

/** One lead in two columns: what the agent proposed (its hypothesis and tests, in its own words) beside what code
 * established (the check, its facts and where they are in the documents). */
function LeadCard({ lead, evidence, subjects, onCite, onView }: LeadProps & { lead: Lead }) {
  const openFinding = (id: string) => {
    const ev = evidence.find((e) => e.evidence_id === id);
    const target = ev && findingTarget(ev);
    return target ? () => onView(target) : null;
  };
  return (
    <li className="lead" id={`lead-${lead.lead_id}`}>
      <div className="finding-top">
        <span className="mono muted">{lead.lead_id}</span>
        <LeadTags lead={lead} />
      </div>
      <div className="lead-cols">
        <div className="lead-claim">
          <strong>{lead.hypothesis}</strong>
          <span className="muted small">Proposal of the {AGENT_TITLE[lead.agent]}</span>
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
        </div>
        <aside className="lead-ledger" aria-label={`Established by code for ${lead.lead_id}`}>
          {lead.evidence_status && <StateMark status={lead.evidence_status} agent={lead.investigation_status} />}
          {lead.check_reason && <p className="small">{lead.check_reason}</p>}
          {Object.keys(lead.check_facts).length > 0 && <Ledger facts={lead.check_facts} />}
          <SubjectLinks lead={lead} subjects={subjects} onView={onView} />
        </aside>
      </div>
    </li>
  );
}

/** Flags: kept leads code verified or corroborated. With none, a line saying so, which points to the AI's
 * unverified suspicions when there are any. */
export function KeptLeads({ leads, withheld, unchecked, confirmed, suspected, ...props }: LeadProps & {
  leads: Lead[];
  /** How many leads the AI supports that code could not verify: shown below, to check by hand */
  suspected: number;
  /** How many verified leads only confirm an existing rule finding */
  confirmed: number;
  /** How many leads the verifier withheld, to tell "none raised" from "none passed" */
  withheld: number;
  /** How many kept leads code did not verify (observations and clear checks) */
  unchecked: number;
}) {
  if (leads.length === 0 && suspected) {
    return (
      <p className="calm check">
        <Icon name="flag" size={16} />
        <span>
          <strong>The AI team found nothing beyond the rule checks.</strong> It has{" "}
          {suspected === 1 ? "1 unconfirmed observation" : `${suspected} unconfirmed observations`} below you may want
          to look at; {suspected === 1 ? "it does" : "they do"} not affect the score or route.
        </span>
      </p>
    );
  }
  if (leads.length === 0) {
    const why = confirmed ? "It agreed with rule findings that already stand (below)."
      : unchecked ? "What it recorded but could not confirm is further down."
      : withheld ? "None of its leads held up against the documents." : "It raised nothing.";
    return (
      <p className="calm">
        <Icon name="done" size={16} />
        <span><strong>The AI team found nothing beyond the rule checks.</strong> {why}</span>
      </p>
    );
  }
  return (
    <>
      <p className="calm check">
        <Icon name="flag" size={16} />
        <span>
          <strong>
            The AI team found {leads.length === 1 ? "1 issue" : `${leads.length} issues`} the rule checks missed.
          </strong>{" "}
          Each was confirmed by code from the documents.
        </span>
      </p>
      <ul className="leads">
        {leads.map((lead) => <LeadCard key={lead.lead_id} lead={lead} {...props} />)}
      </ul>
    </>
  );
}

/** The AI's suspicions code could not check: open, for the analyst to check by hand, yet never a flag. */
export function Suspicions({ leads, ...props }: LeadProps & { leads: Lead[] }) {
  return (
    <>
      <h4 className="memo-head">
        Unconfirmed observations{" "}
        <span className="muted small">(for you to look at: they never raise a flag, a question or the score)</span>
      </h4>
      <ul className="leads">
        {leads.map((lead) => <LeadCard key={lead.lead_id} lead={lead} {...props} />)}
      </ul>
    </>
  );
}

/** Leads that are not flags (observations code could not check, or checks that came back clear): collapsed,
 * never hidden. */
export function CollapsedLeads({ noun, leads, ...props }: LeadProps & { noun: string; leads: Lead[] }) {
  return (
    <details className="disclosure">
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
    <details className="disclosure">
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
