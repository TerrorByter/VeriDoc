import { useState } from "react";
import { flushSync } from "react-dom";
import { AGENT_TITLE, duration, SPECIALISTS, usd } from "../../catalogue";
import type { AgentName, Evidence, ReviewPoint, ViewTarget } from "../../types";
import type { StageProps } from "../workspace/StageDetail";
import { aiLeads, clearChecks, explainedFindings, observations, ruleConfirmed, suspicions, triaged } from "../workspace/stageStatus";
import { ErrorNote } from "../ui";
import { findingTarget } from "./Findings";
import { IssueList, Suspicions } from "./ai-team/Issues";
import { CollapsedLeads, Headline, ReviewedFindings, reviewedFindings, WithheldLeads, WithheldPoints } from "./ai-team/Leads";
import { Trace } from "./ai-team/Trace";
import { Fold, OffNote, Section } from "./common";

/** Memo points, each id a link: a finding id opens its page, a lead id jumps to its card. */
function Points({ points, evidence, onView }: {
  points: ReviewPoint[];
  evidence: Evidence[];
  onView: (target: ViewTarget) => void;
}) {
  const open = (id: string) => {
    const ev = evidence.find((e) => e.evidence_id === id);
    const target = ev && findingTarget(ev);
    if (target) return () => onView(target);
    return document.getElementById(`lead-${id}`)
      ? () => document.getElementById(`lead-${id}`)?.scrollIntoView({ block: "center" })
      : null;
  };
  return (
    <ul className="review-points">
      {points.map((p) => (
        <li key={p.text}>
          <span>{p.text}</span>
          <span className="small muted">
            {p.evidence_ids.map((id, i) => {
              const go = open(id);
              return (
                <span key={id}>
                  {i > 0 && ", "}
                  {go ? <button type="button" className="link mono" onClick={go}>{id}</button> : <span className="mono">{id}</span>}
                </span>
              );
            })}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** The Agents stage, in the order an analyst asks: what did the AI add beyond the rules (issues code confirmed),
 * what did it make of the rules' own findings, what is worth checking by hand, and what would it ask the
 * applicant that the request does not already ask. How the team got there is one fold below. Nothing here feeds
 * the score or the route. */
export function AiTeam({ kase, onView }: StageProps) {
  const [agent, setAgent] = useState<AgentName>("supervisor");
  const [focus, setFocus] = useState<string | null>(null);
  const [auditOpen, setAuditOpen] = useState(false);
  const team = kase.ai_team;

  if (!team) {
    return (
      <Section title="AI agent team" primary aside="Leads never change the score">
        <OffNote stage="ai_team">The rule checks ran in full, so the findings, score and route are complete.</OffNote>
      </Section>
    );
  }

  const report = kase.stages.find((s) => s.stage_id === "ai_team");
  const cost = kase.cost?.usd_by_stage.ai_team;
  const issues = aiLeads(kase);
  const reviewed = reviewedFindings(triaged(kase), ruleConfirmed(kase), explainedFindings(kase));
  const verdicts = { explained: 0, innocent: 0, unsure: 0, agrees: 0 };
  reviewed.forEach((r) => { verdicts[r.verdict] += 1; });
  const suspected = suspicions(kase);
  const unsettled = observations(kase);
  const clear = clearChecks(kase);
  const withheld = team.leads.filter((l) => l.outcome === "withheld");
  const memo = team.memo;
  /** Questions the request does not already ask: one resting only on findings it asks about adds nothing */
  const asked = new Set(kase.asked_findings ?? []);
  const adding = (memo?.questions ?? []).filter((q) => !q.evidence_ids.length || !q.evidence_ids.every((id) => asked.has(id)));
  const repeated = (memo?.questions.length ?? 0) - adding.length;
  /** A question that cites an issue sits on that issue's card; the rest are listed on their own */
  const issueIds = new Set(issues.map((l) => l.lead_id));
  const questions = adding.filter((q) => !q.evidence_ids.some((id) => issueIds.has(id)));
  /** Memo observations that tie leads together; ones citing only rule findings restate them */
  const links = (memo?.observations ?? []).filter((o) => o.evidence_ids.some((id) => id.startsWith("L")));
  const leadProps = { evidence: kase.evidence, subjects: kase.subjects, onView };

  /** Jump to a cited call: open the audit trail, switch to its agent's tab, scroll to the row. */
  const cite = (callId: string) => {
    const call = team.tool_calls.find((c) => c.call_id === callId);
    if (!call) return;
    flushSync(() => {
      setAuditOpen(true);
      setAgent(call.agent);
      setFocus(callId);
    });
    document.getElementById(`call-${callId}`)?.scrollIntoView({ block: "center" });
  };

  return (
    <>
      <Section title="What the AI team added" primary aside="Nothing here changes the score or the route">
        {team.status === "failed" && (
          <ErrorNote>
            {(team.error ?? "The AI team failed").replace(/\.$/, "")}. The rule findings, score and route are complete
            without it.
          </ErrorNote>
        )}
        {team.status === "ran" && (
          <>
            <Headline route={kase.routing?.path ?? null} gate={kase.gate} issues={issues.length} reviewed={verdicts}
              suspected={suspected.length}
              refused={withheld.length + team.tool_calls.filter((c) => c.tool === "record_lead" && c.error).length} />
            {issues.length > 0 && (
              <>
                <h4 className="memo-head">Issues the rules missed <span className="muted small">(each confirmed by code from the documents)</span></h4>
                <IssueList leads={issues} questions={adding} onCite={cite} {...leadProps} />
                {links.length > 0 && <Points points={links} evidence={kase.evidence} onView={onView} />}
              </>
            )}
            {reviewed.length > 0 && (
              <>
                <h4 className="memo-head">Rule findings it reviewed <span className="muted small">(the rule's flag and score stand whatever the AI says)</span></h4>
                <ReviewedFindings rows={reviewed} evidence={kase.evidence} signals={kase.signals} onView={onView} />
              </>
            )}
            {suspected.length > 0 && (
              <>
                <h4 className="memo-head">Worth checking by hand <span className="muted small">(suspicions no code check could confirm: never a flag, a question or the score)</span></h4>
                <Suspicions leads={suspected} onCite={cite} {...leadProps} />
              </>
            )}
            {questions.length > 0 && (
              <>
                <h4 className="memo-head">{issues.length ? "Other questions" : "Questions"} for the applicant <span className="muted small">(drafts for the analyst to approve; they join the request)</span></h4>
                <Points points={questions} evidence={kase.evidence} onView={onView} />
              </>
            )}
            {repeated > 0 && (
              <p className="muted small">
                {repeated === 1 ? "1 more question was" : `${repeated} more questions were`} left out: the request
                already asks about {repeated === 1 ? "that finding" : "those findings"}.
              </p>
            )}
          </>
        )}
      </Section>

      <Fold title="How the team worked" open={auditOpen} onToggle={setAuditOpen}
        aside={`${team.tool_calls.length} steps${report ? `, ${duration(report.duration_ms)}` : ""}${cost ? `, US${usd(cost, 2)}` : ""}`}>
        <dl className="facts">
          <div><dt>Supervisor model</dt><dd className="mono">{team.models.supervisor ?? "—"}</dd></div>
          <div><dt>Specialist model</dt><dd className="mono">{team.models.specialist ?? "—"}</dd></div>
        </dl>
        {team.notes.length > 0 && (
          <ul className="reasons muted small">
            {team.notes.map((n) => <li key={n}>{n}</li>)}
          </ul>
        )}
        {team.status === "ran" && (
          <>
            <h4 className="memo-head">Supervisor plan <span className="muted small">(each specialist is given only its own brief)</span></h4>
            <ul className="team-plan">
              {SPECIALISTS.filter((s) => team.plan[s]).map((s) => (
                <li key={s}>
                  <strong>{AGENT_TITLE[s]}</strong>
                  <p className="muted">{team.plan[s]}</p>
                </li>
              ))}
            </ul>
            <h4 className="memo-head">Every step <span className="muted small">(read-only tools; full results stay on the server)</span></h4>
            <Trace calls={team.tool_calls} agent={agent} focus={focus} onAgent={setAgent} onView={onView} />
            {clear.length > 0 && (
              <>
                <h4 className="memo-head">Checks that came back clear <span className="muted small">(a record of what was looked at, not a flag)</span></h4>
                <CollapsedLeads noun="clear check" leads={clear} onCite={cite} {...leadProps} />
              </>
            )}
            {unsettled.length > 0 && (
              <>
                <h4 className="memo-head">Could not settle <span className="muted small">(never a flag or a question)</span></h4>
                <CollapsedLeads noun="unsettled observation" leads={unsettled} onCite={cite} {...leadProps} />
              </>
            )}
            {(withheld.length > 0 || (memo?.withheld ?? 0) > 0) && (
              <>
                <h4 className="memo-head">Withheld by the verifier <span className="muted small">(kept on the record with the reason)</span></h4>
                {withheld.length > 0 && <WithheldLeads leads={withheld} />}
                {memo && memo.withheld > 0 && <WithheldPoints points={memo.withheld_points ?? []} count={memo.withheld} />}
              </>
            )}
          </>
        )}
      </Fold>
    </>
  );
}
