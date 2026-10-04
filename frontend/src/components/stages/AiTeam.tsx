import { useState } from "react";
import { flushSync } from "react-dom";
import { AGENT_TITLE, duration, SPECIALISTS, usd } from "../../catalogue";
import type { AgentName, ReviewPoint } from "../../types";
import type { StageProps } from "../workspace/StageDetail";
import { keptLeads } from "../workspace/stageStatus";
import { ErrorNote } from "../ui";
import { KeptLeads, WithheldLeads } from "./ai-team/Leads";
import { Trace } from "./ai-team/Trace";
import { OffNote, Section } from "./common";

function Points({ points }: { points: ReviewPoint[] }) {
  return (
    <ul className="review-points">
      {points.map((p) => (
        <li key={p.text}>
          <span>{p.text}</span>
          <span className="mono small muted">{p.evidence_ids.join(", ")}</span>
        </li>
      ))}
    </ul>
  );
}

/** The AI agent team: what it was briefed, every tool call it made, the leads
 * the verifier kept or withheld, and the Supervisor's memo. Nothing here
 * feeds the score or the route. */
export function AiTeam({ kase, onView }: StageProps) {
  const [agent, setAgent] = useState<AgentName>("supervisor");
  const [focus, setFocus] = useState<string | null>(null);
  const team = kase.ai_team;
  const aside = "Leads never change the score";

  if (!team) {
    return (
      <Section title="AI agent team" aside={aside}>
        <OffNote stage="ai_team">The rule sweep above ran in full, so the findings, score and route are complete.</OffNote>
      </Section>
    );
  }

  const report = kase.stages.find((s) => s.stage_id === "ai_team");
  const cost = kase.cost?.usd_by_stage.ai_team;
  const kept = keptLeads(kase);
  const withheld = team.leads.filter((l) => l.outcome === "withheld");
  const memo = team.memo;
  /** The team's note when the Supervisor ended without calling submit_memo */
  const noMemo = team.notes.includes("supervisor: no memo submitted");

  /** Jump to a cited call, switching to its agent's tab first. */
  const cite = (callId: string) => {
    const call = team.tool_calls.find((c) => c.call_id === callId);
    if (!call) return;
    flushSync(() => {
      setAgent(call.agent);
      setFocus(callId);
    });
    document.getElementById(`call-${callId}`)?.scrollIntoView({ block: "center" });
  };

  return (
    <>
      <Section title="AI agent team" aside={aside}>
        <dl className="facts">
          <div><dt>Supervisor model</dt><dd className="mono">{team.models.supervisor ?? "—"}</dd></div>
          <div><dt>Specialist model</dt><dd className="mono">{team.models.specialist ?? "—"}</dd></div>
          <div>
            <dt>Time and cost</dt>
            <dd className="mono">{report ? duration(report.duration_ms) : "—"}{cost ? `, ${usd(cost, 4)}` : ""}</dd>
          </div>
          <div><dt>Tool calls</dt><dd className="mono">{team.tool_calls.length}</dd></div>
        </dl>
        {team.notes.length > 0 && (
          <ul className="reasons muted small">
            {team.notes.map((n) => <li key={n}>{n}</li>)}
          </ul>
        )}
        {team.status === "failed" && (
          <ErrorNote>
            {(team.error ?? "The AI team failed").replace(/\.$/, "")}. The rule findings, score and route are complete
            without it.
          </ErrorNote>
        )}
      </Section>

      {team.status === "ran" && (
        <>
          <Section title="Supervisor plan" aside="Each specialist is given only its own brief">
            <ul className="team-plan">
              {SPECIALISTS.filter((s) => team.plan[s]).map((s) => (
                <li key={s}>
                  <strong>{AGENT_TITLE[s]}</strong>
                  <p className="muted">{team.plan[s]}</p>
                </li>
              ))}
            </ul>
          </Section>

          <Section title="Investigation trace" aside="Read-only tools; full results stay on the server">
            <Trace calls={team.tool_calls} agent={agent} focus={focus} onAgent={setAgent} onView={onView} />
          </Section>

          <Section title="AI leads" aside="Each lead cites the tool calls it rests on">
            <KeptLeads leads={kept} withheld={withheld.length} evidence={kase.evidence} onCite={cite} onView={onView} />
          </Section>

          {withheld.length > 0 && (
            <Section title="Withheld by the verifier" aside="Kept on the record with the reason">
              <WithheldLeads leads={withheld} />
            </Section>
          )}

          <Section
            title="Case memo"
            aside={memo && memo.withheld > 0 ? `${memo.withheld} unsupported point${memo.withheld === 1 ? "" : "s"} withheld` : undefined}
          >
            {memo && (memo.observations.length > 0 || memo.questions.length > 0) ? (
              <>
                {memo.observations.length > 0 && (
                  <>
                    <p className="muted small">Observations</p>
                    <Points points={memo.observations} />
                  </>
                )}
                {memo.questions.length > 0 && (
                  <>
                    <p className="muted small">Questions for the applicant</p>
                    <Points points={memo.questions} />
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
        </>
      )}
    </>
  );
}
