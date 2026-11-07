import { Fragment } from "react";
import { AGENT_TITLE, EVIDENCE_LABEL, FACT_LABEL, LEAD_STATUS_LABEL, PATH_LABEL, PATTERN_LABEL } from "../../../catalogue";
import type { DocRef, Evidence, EvidenceStatus, GateDecision, Lead, ReviewPath, ViewTarget, WithheldPoint } from "../../../types";
import { Icon, LEAD_STATUS_TONE, Pill, type IconName, type Tone } from "../../ui";
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

/** "SGD 1,946,464.82" from "1946464.82" */
export const sgdAmount = (value: string) =>
  `SGD ${Number(value).toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const factValue = (key: string, value: string) =>
  key === "amount" && value.trim() !== "" && !Number.isNaN(Number(value)) ? sgdAmount(value) : value;

/** The facts a code check read, as a ruled ledger. */
export function Ledger({ facts }: { facts: Record<string, string> }) {
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

export interface LeadProps {
  evidence: Evidence[];
  /** Bank rows, invoices and counterparties -> the page they are on */
  subjects: Record<string, DocRef>;
  /** Show a cited call in the trace */
  onCite: (callId: string) => void;
  onView: (target: ViewTarget) => void;
}

/** What the agent proposed, in its own words: its hypothesis, each test with the tool results it rests on, what
 * it was missing and the findings it judged. */
export function LeadClaim({ lead, evidence, onCite, onView }: Omit<LeadProps, "subjects"> & { lead: Lead }) {
  const openFinding = (id: string) => {
    const ev = evidence.find((e) => e.evidence_id === id);
    const target = ev && findingTarget(ev);
    return target ? () => onView(target) : null;
  };
  return (
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
  );
}

/** One lead in two columns: what the agent proposed (its hypothesis and tests, in its own words) beside what code
 * established (the check, its facts and where they are in the documents). */
export function LeadCard({ lead, evidence, subjects, onCite, onView }: LeadProps & { lead: Lead }) {
  return (
    <li className="lead" id={`lead-${lead.lead_id}`}>
      <div className="finding-top">
        <span className="mono muted">{lead.lead_id}</span>
        <LeadTags lead={lead} />
      </div>
      <div className="lead-cols">
        <LeadClaim lead={lead} evidence={evidence} onCite={onCite} onView={onView} />
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

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** The tab's first line: what the AI added beyond the rules, set against the route the rules chose (an issue on a
 * fast-track case is the point of the team), then what the shadow gate would do with it and what the AI made of the
 * rules' own findings. Never colour alone: an icon and words. */
export function Headline({ route, gate, issues, reviewed, suspected, refused }: {
  /** The route the rules chose */
  route: ReviewPath | null;
  /** The route the disposition gate would take with the AI's verified leads: logged, never applied */
  gate: GateDecision | null;
  /** Issues the rules missed that code confirmed */
  issues: number;
  /** The rule findings it reviewed, by verdict */
  reviewed: Record<Verdict, number>;
  /** Patterns it suspects that code could not check */
  suspected: number;
  /** Leads the verifier refused: said, so "nothing found" never hides "found, but not recorded" */
  refused: number;
}) {
  const total = Object.values(reviewed).reduce((a, b) => a + b, 0);
  const parts = [
    reviewed.agrees && `agreed with ${plural(reviewed.agrees, "finding")}`,
    reviewed.innocent && `suggested an ordinary explanation for ${reviewed.innocent}`,
    reviewed.unsure && `could not settle ${reviewed.unsure}`,
  ].filter(Boolean);
  const review = total ? ` Of the ${plural(total, "rule finding")} it reviewed, it ${parts.join("; ")}.` : "";
  const byHand = suspected ? ` ${plural(suspected, "suspicion")} it could not confirm ${suspected === 1 ? "is" : "are"} below, to check by hand.` : "";
  const held = refused ? ` The verifier refused ${plural(refused, "lead")} it tried to record (see How the team worked).` : "";
  const found = plural(issues, "issue");
  const lead = !issues
    ? "The AI team found nothing the rule checks missed."
    : route === "fast_track"
      ? `The rules would fast-track this case. The AI team found ${found} they cannot see.`
      : `The AI team found ${found} the rule checks cannot see.`;
  const raised = issues > 0 && route && gate && gate.would_route !== route;
  return (
    <div className={`calm ai-headline${issues ? " check" : ""}`}>
      <Icon name={issues ? "flag" : "done"} size={16} />
      <div>
        <p className="ai-headline-lead">{lead}</p>
        {issues > 0 && (
          <p>
            {issues === 1 ? "It is" : "Each is"} confirmed by code from the documents.
            {raised && ` With ${issues === 1 ? "it" : "them"}, the AI route would be ${PATH_LABEL[gate.would_route].toLowerCase()}, not ${PATH_LABEL[route].toLowerCase()}; that route is logged, not applied, so the analyst decides.`}
          </p>
        )}
        {(review || byHand || held) && <p className="muted small">{`${review}${byHand}${held}`.trim()}</p>}
      </div>
    </div>
  );
}

/** Issues the rules missed, code-confirmed: the AI team's own flags, as full cards. */
export function LeadList({ leads, ...props }: LeadProps & { leads: Lead[] }) {
  return (
    <ul className="leads">
      {leads.map((lead) => <LeadCard key={lead.lead_id} lead={lead} {...props} />)}
    </ul>
  );
}

/** What the AI made of a rule finding: it suggests an ordinary explanation, it could not tell, or it agrees the
 * finding stands */
export type Verdict = "innocent" | "unsure" | "agrees";

const VERDICT: Record<Verdict, { label: string; tone: Tone; title: string }> = {
  innocent: { label: "Ordinary explanation to check", tone: "med", title: "The AI's view: check it before relying on it" },
  unsure: { label: "Could not tell", tone: "neutral", title: "The AI could not settle it from the documents" },
  agrees: { label: "Agrees it stands", tone: "neutral", title: "The AI found nothing that explains the finding" },
};
const VERDICT_ORDER: Verdict[] = ["innocent", "unsure", "agrees"];

export interface Reviewed {
  findingId: string;
  verdict: Verdict;
  /** Why, in the AI's or code's words; empty when it only agrees */
  reason: string;
}

/** One row per rule finding the AI reviewed, its verdict and why. */
export function reviewedFindings(triage: Lead[], confirmed: Lead[]): Reviewed[] {
  const ids = [...new Set([...triage.flatMap((l) => l.finding_ids),
    ...confirmed.map((l) => l.check_facts.rule_finding)])];
  const rows = ids.map((id): Reviewed => {
    const on = triage.filter((l) => l.finding_ids.includes(id));
    const innocent = on.find((l) => l.investigation_status === "refuted");
    if (innocent) return { findingId: id, verdict: "innocent", reason: innocent.tests.map((t) => t.observed).join("; ") };
    const unsure = on.find((l) => l.investigation_status === "unresolved");
    if (unsure) return { findingId: id, verdict: "unsure", reason: unsure.missing_evidence.length ? `Would need: ${unsure.missing_evidence.join("; ")}` : "" };
    const byCode = confirmed.find((l) => l.check_facts.rule_finding === id);
    return { findingId: id, verdict: "agrees", reason: byCode?.check_reason ? `Code agrees: ${byCode.check_reason}` : "" };
  });
  return rows.sort((a, b) => VERDICT_ORDER.indexOf(a.verdict) - VERDICT_ORDER.indexOf(b.verdict)
    || a.findingId.localeCompare(b.findingId));
}

/** The rule findings the AI reviewed: one compact row each, so agreeing with a rule never reads as a new issue. */
export function ReviewedFindings({ rows, evidence, signals, onView }: {
  rows: Reviewed[];
  evidence: Evidence[];
  signals: Record<string, { name: string }>;
  onView: (target: ViewTarget) => void;
}) {
  return (
    <ul className="reviewed">
      {rows.map(({ findingId, verdict, reason }) => {
        const ev = evidence.find((e) => e.evidence_id === findingId);
        const target = ev && findingTarget(ev);
        const { label, tone, title } = VERDICT[verdict];
        return (
          <li key={findingId}>
            <span className="reviewed-finding">
              {target
                ? <button type="button" className="link mono" onClick={() => onView(target)}>{findingId}</button>
                : <span className="mono">{findingId}</span>}{" "}
              {ev ? signals[ev.check_id]?.name ?? ev.check_id : "Rule finding"}
            </span>
            <Pill tone={tone} title={title}>{label}</Pill>
            {reason && <span className="reviewed-reason small muted">{reason}</span>}
          </li>
        );
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

/** Leads that are not flags (unsettled observations, checks that came back clear): collapsed, never hidden. */
export function CollapsedLeads({ noun, leads, ...props }: LeadProps & { noun: string; leads: Lead[] }) {
  return (
    <details className="disclosure">
      <summary>Show {plural(leads.length, noun)}</summary>
      <LeadList leads={leads} {...props} />
    </details>
  );
}

/** Memo points the verifier dropped, with what each cited and why: collapsed, never hidden. */
export function WithheldPoints({ points, count }: { points: WithheldPoint[]; count: number }) {
  const unreadable = count - points.length;
  return (
    <details className="disclosure">
      <summary>Show {plural(count, "withheld memo point")}</summary>
      <ul className="leads">
        {points.map((p, i) => (
          <li key={i} className="lead withheld">
            <div className="finding-top">
              <span className="muted small">{p.section === "question" ? "Question" : "Observation"}</span>
              {p.evidence_ids.length > 0 && <span className="mono muted small">cites {p.evidence_ids.join(", ")}</span>}
            </div>
            <strong>{p.text}</strong>
            <p className="small">Withheld: {p.reason}</p>
          </li>
        ))}
      </ul>
      {unreadable > 0 && (
        <p className="muted small">
          {points.length ? `${plural(unreadable, "other")}: ` : ""}not kept as text (malformed, or from a case run
          before withheld points were kept).
        </p>
      )}
    </details>
  );
}
