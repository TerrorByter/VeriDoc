import { AGENT_TITLE, PATTERN_LABEL } from "../../../catalogue";
import type { DocRef, Lead, ReviewPoint, ViewTarget } from "../../../types";
import { Icon, Pill } from "../../ui";
import { LeadCard, LeadClaim, Ledger, type LeadProps, sgdAmount, StateMark } from "./Leads";

/** One line of an issue: a document or bank row it rests on, as code read it */
interface Line {
  role: string;
  /** The id that opens it in its document (a bank row T.., an invoice number, a party P..) */
  ref?: string;
  /** Shown in place of the ref, e.g. a UEN for a party */
  label?: string;
  date?: string;
  /** Signed: money out of the applicant's account is negative */
  amount?: { value: string; out: boolean };
  party?: string;
}

/** An issue told from what code established: the event, why it matters to a lender, why the rule checks cannot
 * see it, and the lines it rests on. Never from the agent's prose, which the card keeps in a fold. */
interface Story {
  title: string;
  matters: string;
  missed: string;
  lines: Line[];
}

const days = (from: string, to: string) => Math.round((Date.parse(to) - Date.parse(from)) / 86_400_000);

/** A sentence's full stop, unless it already ends in one (a name such as "Sdn. Bhd.") */
const sentence = (text: string) => (text.endsWith(".") ? text : `${text}.`);

function story(lead: Lead): Story | null {
  const f = lead.check_facts;
  switch (lead.pattern) {
    case "round_trip": {
      const share = (Number(f.back_amount) / Number(f.out_amount)) * 100;
      return {
        title: `${sgdAmount(f.out_amount)} went to ${f.counterparty}, and ${sgdAmount(f.back_amount)} `
          + `(${share.toFixed(1)}%) came back from it ${days(f.out_date, f.back_date)} days later.`,
        matters: "Money that leaves and returns from the same party can be booked as sales, so the trade looks "
          + "larger than it is.",
        missed: "The rules match each payment to an invoice; money coming back from a supplier is not checked "
          + "against what was paid out.",
        lines: [
          { role: "Paid out", ref: f.out_transaction, date: f.out_date, amount: { value: f.out_amount, out: true },
            party: f.counterparty },
          { role: "Came back", ref: f.back_transaction, date: f.back_date, amount: { value: f.back_amount, out: false },
            party: f.counterparty },
        ],
      };
    }
    case "third_party_payment":
      return {
        title: sentence(`Invoice ${f.invoice} was paid by ${f.payer}, not by its buyer, ${f.buyer}`),
        matters: "When someone other than the buyer settles a sale, the sale, the debt or the money may not be "
          + "what the invoice says.",
        missed: "The rules match each invoice to a payment by amount and date, not by who paid it.",
        lines: [
          { role: "Invoice", ref: f.invoice, party: `Buyer: ${f.buyer}` },
          { role: "Paid by", ref: f.transaction, date: f.date, amount: { value: f.amount, out: false }, party: f.payer },
        ],
      };
    case "related_party":
      return {
        title: `${f.shared_officer}, the applicant's ${f.applicant_role}, is also an officer of ${f.counterparty}, `
          + "a company it trades with.",
        matters: "Trade with a company the applicant's own people run is not at arm's length: its prices and "
          + "volumes need independent support.",
        missed: "The rules check that each counterparty is registered and live, not who runs it.",
        lines: [
          { role: "Applicant", party: `${f.shared_officer}, ${f.applicant_role}` },
          { role: "Counterparty", ref: lead.counterparty ?? undefined, label: `UEN ${f.register_uen}`,
            party: `${f.counterparty}; officer ${f.shared_officer}` },
        ],
      };
    default:
      return null;
  }
}

/** The lines an issue rests on, each with a button to its place in the documents. */
function IssueLines({ lines, subjects, onView }: {
  lines: Line[];
  subjects: Record<string, DocRef>;
  onView: (target: ViewTarget) => void;
}) {
  const dated = lines.some((l) => l.date || l.amount);
  return (
    <div className="table-wrap issue-lines">
      <table className="data">
        <thead>
          <tr>
            <th scope="col"><span className="sr-only">Role</span></th>
            <th scope="col">Reference</th>
            {dated && <th scope="col">Date</th>}
            {dated && <th scope="col" className="num">Amount</th>}
            <th scope="col">Party</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l) => {
            const at = l.ref ? subjects[l.ref] : undefined;
            return (
              <tr key={l.role}>
                <th scope="row">{l.role}</th>
                <td>
                  {at ? (
                    <button type="button" className="link mono" title="Show in document"
                      onClick={() => onView({ docId: at.doc_id, page: at.page })}>
                      <Icon name="doc" size={14} /> {l.label ?? l.ref}
                    </button>
                  ) : (l.label ?? l.ref) ? (
                    <span className="mono">{l.label ?? l.ref}</span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                {dated && <td className="mono">{l.date ?? ""}</td>}
                {dated && (
                  <td className={`num mono${l.amount ? (l.amount.out ? " money-out" : " money-in") : ""}`}>
                    {l.amount ? `${l.amount.out ? "−" : "+"}${sgdAmount(l.amount.value)}` : ""}
                  </td>
                )}
                <td>{l.party ?? ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** An issue the rules missed, confirmed by code: the event, why it matters, the lines it rests on and the
 * question it raises, with the agent's own working folded away. */
function IssueCard({ lead, questions, ...props }: LeadProps & { lead: Lead; questions: ReviewPoint[] }) {
  const told = story(lead);
  return (
    <li className="issue" id={`lead-${lead.lead_id}`}>
      <div className="issue-top">
        {lead.pattern && <Pill tone="crit">{PATTERN_LABEL[lead.pattern]}</Pill>}
        {lead.evidence_status && <StateMark status={lead.evidence_status} agent={lead.investigation_status} />}
        <span className="spacer" />
        <span className="muted small">Found by the {AGENT_TITLE[lead.agent]} · <span className="mono">{lead.lead_id}</span></span>
      </div>
      <h5 className="issue-title">{told?.title ?? lead.check_reason ?? lead.hypothesis}</h5>
      {told && (
        <dl className="issue-why">
          <dt>Why it matters</dt><dd>{told.matters}</dd>
          <dt>Why the rules miss it</dt><dd>{told.missed}</dd>
        </dl>
      )}
      {told ? <IssueLines lines={told.lines} subjects={props.subjects} onView={props.onView} />
        : <Ledger facts={lead.check_facts} />}
      {questions.length > 0 && (
        <div className="issue-ask">
          <span className="issue-ask-label">Ask the applicant</span>
          {questions.map((q) => <p key={q.text}>{q.text}</p>)}
        </div>
      )}
      <details className="disclosure">
        <summary>How the AI found it</summary>
        <div className="issue-working">
          <LeadClaim lead={lead} evidence={props.evidence} onCite={props.onCite} onView={props.onView} />
          {lead.check_reason && (
            <p className="small"><span className="muted">Code check:</span> {lead.check_reason}</p>
          )}
        </div>
      </details>
    </li>
  );
}

/** Issues the rules missed, each with the questions that cite it. */
export function IssueList({ leads, questions, ...props }: LeadProps & { leads: Lead[]; questions: ReviewPoint[] }) {
  return (
    <ul className="issues">
      {leads.map((lead) => (
        <IssueCard key={lead.lead_id} lead={lead} {...props}
          questions={questions.filter((q) => q.evidence_ids.includes(lead.lead_id))} />
      ))}
    </ul>
  );
}

/** Suspicions no code check could confirm: one line each, the full lead a click away. */
export function Suspicions({ leads, ...props }: LeadProps & { leads: Lead[] }) {
  return (
    <ul className="suspicions">
      {leads.map((lead) => (
        <li key={lead.lead_id}>
          <details>
            <summary>
              <Icon name="right" size={14} />
              {lead.pattern && <span className="suspicion-kind">{PATTERN_LABEL[lead.pattern]}</span>}
              <span className="suspicion-text">{lead.hypothesis}</span>
              <span className="muted small suspicion-agent">{AGENT_TITLE[lead.agent]}</span>
            </summary>
            <ul className="leads">
              <LeadCard lead={lead} {...props} />
            </ul>
          </details>
        </li>
      ))}
    </ul>
  );
}
