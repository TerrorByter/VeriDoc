import { useState } from "react";
import { flushSync } from "react-dom";
import { AGENT_TITLE, duration, SPECIALISTS, usd } from "../../catalogue";
import type { AgentName, Evidence, ReviewPoint, ViewTarget } from "../../types";
import type { StageProps } from "../workspace/StageDetail";
import { aiLeads, clearChecks, observations, ruleConfirmed, suspicions } from "../workspace/stageStatus";
import { ErrorNote } from "../ui";
import { findingTarget } from "./Findings";
import { CollapsedLeads, KeptLeads, RuleConfirmations, Suspicions, WithheldLeads } from "./ai-team/Leads";
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

/** The Agents stage: what the AI agent team found and what the Supervisor wrote, first; then how the team got
 * there, folded: its plan, every tool call, and the leads the verifier could not turn into flags. Nothing here
 * feeds the score or the route. */
export function AiTeam({ kase, onView }: StageProps) {
  const [agent, setAgent] = useState<AgentName>("supervisor");
  const [focus, setFocus] = useState<string | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
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
  const leads = aiLeads(kase);
  const clear = clearChecks(kase);
  const suspected = suspicions(kase);
  const unchecked = observations(kase);
  const confirmed = ruleConfirmed(kase);
  const withheld = team.leads.filter((l) => l.outcome === "withheld");
  const memo = team.memo;
  /** The team's note when the Supervisor ended without calling submit_memo */
  const noMemo = team.notes.includes("supervisor: no memo submitted");
  const leadProps = { evidence: kase.evidence, subjects: kase.subjects, onView };

  /** Jump to a cited call: open the trace, switch to its agent's tab, scroll to the row. */
  const cite = (callId: string) => {
    const call = team.tool_calls.find((c) => c.call_id === callId);
    if (!call) return;
    flushSync(() => {
      setTraceOpen(true);
      setAgent(call.agent);
      setFocus(callId);
    });
    document.getElementById(`call-${callId}`)?.scrollIntoView({ block: "center" });
  };

  return (
    <>
      <Section title="What the investigation found" primary aside="AI leads are proposals; none changes the score">
        {team.status === "failed" && (
          <ErrorNote>
            {(team.error ?? "The AI team failed").replace(/\.$/, "")}. The rule findings, score and route are complete
            without it.
          </ErrorNote>
        )}
        {team.status === "ran" && (
          <>
            <KeptLeads leads={leads} withheld={withheld.length} unchecked={unchecked.length + clear.length}
              confirmed={confirmed.length} suspected={suspected.length} onCite={cite} {...leadProps} />
            {confirmed.length > 0 && <RuleConfirmations leads={confirmed} evidence={kase.evidence} />}
            {suspected.length > 0 && <Suspicions leads={suspected} onCite={cite} {...leadProps} />}
          </>
        )}
      </Section>

      {team.status === "ran" && (
        <Section
          title="Case memo"
          primary
          aside={memo && memo.withheld > 0 ? `${memo.withheld} unsupported point${memo.withheld === 1 ? "" : "s"} withheld` : "Drafted by the Supervisor from the leads above"}
        >
          {memo && (memo.observations.length > 0 || memo.questions.length > 0) ? (
            <>
              {memo.observations.length > 0 && (
                <>
                  <h4 className="memo-head">Observations</h4>
                  <Points points={memo.observations} evidence={kase.evidence} onView={onView} />
                </>
              )}
              {memo.questions.length > 0 && (
                <>
                  <h4 className="memo-head">Questions for the applicant <span className="muted small">(drafts for the analyst to approve)</span></h4>
                  <Points points={memo.questions} evidence={kase.evidence} onView={onView} />
                </>
              )}
            </>
          ) : (
            <p className="muted">
              {noMemo
                ? "The Supervisor submitted no memo."
                : memo && memo.withheld > 0
                  ? "Every memo point was withheld by the verifier."
                  : "The Supervisor's memo had no points."}
            </p>
          )}
        </Section>
      )}

      <Fold title="How the team got here" aside={`${team.tool_calls.length} tool calls${report ? `, ${duration(report.duration_ms)}` : ""}${cost ? `, ${usd(cost, 4)}` : ""}`}>
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
          </>
        )}
      </Fold>

      {team.status === "ran" && (
        <>
          <Fold title="Investigation trace" aside="Read-only tools; full results stay on the server" open={traceOpen} onToggle={setTraceOpen}>
            <Trace calls={team.tool_calls} agent={agent} focus={focus} onAgent={setAgent} onView={onView} />
          </Fold>

          {unchecked.length > 0 && (
            <Fold title="AI observations, not checked" aside="No code check could establish these: never a flag or a question">
              <CollapsedLeads noun="observation" leads={unchecked} onCite={cite} {...leadProps} />
            </Fold>
          )}

          {clear.length > 0 && (
            <Fold title="Checks that came back clear" aside="A record of what was checked, not a flag">
              <CollapsedLeads noun="clear check" leads={clear} onCite={cite} {...leadProps} />
            </Fold>
          )}

          {withheld.length > 0 && (
            <Fold title="Withheld by the verifier" aside="Kept on the record with the reason">
              <WithheldLeads leads={withheld} />
            </Fold>
          )}
        </>
      )}
    </>
  );
}
