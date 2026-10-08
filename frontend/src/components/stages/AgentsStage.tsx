import { useState } from "react";
import { duration } from "../../catalogue";
import type { Category } from "../../types";
import type { StageProps } from "../workspace/StageDetail";
import { explainedFindings } from "../workspace/stageStatus";
import { tabKeys } from "../ui";
import { AiTeam } from "./AiTeam";
import { Fold, Section } from "./common";
import { Findings } from "./Findings";

const TABS: { key: Category; label: string }[] = [
  { key: "fraud_signal", label: "Fraud signals" },
  { key: "compliance", label: "Compliance" },
  { key: "completeness", label: "Completeness" },
];
const TAB_KEYS = TABS.map((t) => t.key);

export function AgentsStage(props: StageProps) {
  const { kase } = props;
  const [agent, setAgent] = useState<string | null>(null);
  const [tab, setTab] = useState<Category>("fraud_signal");
  const explained = explainedFindings(kase);
  const risky = kase.evidence.filter((e) => e.severity !== "info");
  const inScope = risky.filter((e) => agent === null || e.agent === agent);
  // A finding code explained is not an open one: the tab counts show what still needs the analyst
  const count = (c: Category) => inScope.filter((e) => e.category === c && !(e.evidence_id in explained)).length;

  return (
    <>
      <AiTeam {...props}>
        <Section
          title={agent ? `Rule findings from ${agent}` : "Rule findings"}
          aside={agent ? <button type="button" className="link" onClick={() => setAgent(null)}>Show all rule groups</button> : "From the rule checks, before any AI"}
        >
          <div className="segmented" role="tablist" aria-label="Finding category" onKeyDown={tabKeys(TAB_KEYS, tab, setTab)}>
            {TABS.map(({ key, label }) => (
              <button
                key={key}
                id={`finding-tab-${key}`}
                type="button"
                role="tab"
                aria-selected={tab === key}
                aria-controls="finding-panel"
                tabIndex={tab === key ? 0 : -1}
                onClick={() => setTab(key)}
              >
                {label} <span className="count">{count(key)}</span>
              </button>
            ))}
          </div>
          <div id="finding-panel" role="tabpanel" aria-labelledby={`finding-tab-${tab}`}>
            <Findings {...props} evidence={inScope.filter((e) => e.category === tab)} explained={explained}
              empty="No findings in this category" />
          </div>
        </Section>
      </AiTeam>

      <Fold title="Mandatory tools: rule sweep" aside="Run in parallel; select a rule group to filter the findings above">
        <div className="table-wrap">
          <table className="data">
            <caption className="sr-only">Rule groups, their checks and findings</caption>
            <thead>
              <tr>
                <th scope="col">Rule group</th>
                <th scope="col" className="num">Checks</th>
                <th scope="col" className="num">Findings</th>
                <th scope="col" className="num">Time</th>
              </tr>
            </thead>
            <tbody>
              {kase.agent_reports.map((a) => (
                <tr
                  key={a.name}
                  className={`clickable${agent === a.name ? " selected" : ""}`}
                  onClick={() => setAgent(agent === a.name ? null : a.name)}
                >
                  <td>
                    <button type="button" className="row-button" aria-pressed={agent === a.name} title={a.description}>
                      {a.name}
                    </button>
                    <span className="muted small block">{a.description}</span>
                  </td>
                  <td className="num">{a.checks_run}</td>
                  <td className="num">
                    <span className={`count${a.findings ? " hot" : ""}`}>{a.findings}</span>
                  </td>
                  <td className="num mono muted">{duration(a.duration_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Fold>
    </>
  );
}
