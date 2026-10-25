import { useState } from "react";
import { navigate } from "../../route";
import type { StageProps } from "../workspace/StageDetail";
import { aiLeads } from "../workspace/stageStatus";
import { LeadTags } from "./ai-team/Leads";
import { Section } from "./common";
import { Chains } from "./investigation/Chains";
import { Queue } from "./investigation/Queue";
import { Timeline } from "./investigation/Timeline";

type View = "queue" | "chains" | "timeline";

/** The AI team's kept leads, beside the rule tasks; each opens stage 6 for its trace. */
function LeadsToReview({ kase }: StageProps) {
  const team = kase.ai_team;
  if (!team) return null;
  const leads = aiLeads(kase);
  return (
    <Section title="AI leads to review" aside="Leads never change the score">
      {team.status === "failed" ? (
        <p className="muted">The AI team failed on this case; the rule tasks above are complete without it.</p>
      ) : leads.length === 0 ? (
        <p className="muted">The AI team raised no leads.</p>
      ) : (
        <ul className="leads">
          {leads.map((lead) => (
            <li key={lead.lead_id} className="lead">
              <div className="finding-top">
                <span className="mono muted">{lead.lead_id}</span>
                <LeadTags lead={lead} />
              </div>
              <button
                type="button"
                className="link"
                onClick={() => navigate({ screen: "case", caseId: kase.case_id, stage: "agents" })}
              >
                {lead.hypothesis}
              </button>
              <p className="muted small">{lead.tests.map((t) => t.observed).join("; ")}</p>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

export function InvestigationStage(props: StageProps) {
  const { kase } = props;
  const [view, setView] = useState<View>("queue");
  const broken = kase.chains.filter((c) => c.broken).length;
  const anomalies = kase.timeline.filter((e) => e.evidence_ids.length > 0).length;
  const tabs: { key: View; label: string; count: number; hot: boolean }[] = [
    { key: "queue", label: "Investigation queue", count: kase.investigation.length, hot: kase.investigation.length > 0 },
    { key: "chains", label: "Transaction chains", count: kase.chains.length, hot: broken > 0 },
    { key: "timeline", label: "Timeline", count: kase.timeline.length, hot: anomalies > 0 },
  ];
  return (
    <>
      <div className="segmented" role="tablist" aria-label="Investigation view">
        {tabs.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={view === t.key} onClick={() => setView(t.key)}>
            {t.label} <span className={`count${t.hot ? " hot" : ""}`}>{t.count}</span>
          </button>
        ))}
      </div>
      {view === "queue" && (
        <>
          <Queue {...props} />
          <LeadsToReview {...props} />
        </>
      )}
      {view === "chains" && <Chains {...props} />}
      {view === "timeline" && <Timeline {...props} />}
    </>
  );
}
