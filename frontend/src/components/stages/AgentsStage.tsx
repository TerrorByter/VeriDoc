import { useState } from "react";
import { duration } from "../../catalogue";
import type { Category } from "../../types";
import type { StageProps } from "../workspace/StageDetail";
import { AiTeam } from "./AiTeam";
import { Section } from "./common";
import { Findings } from "./Findings";

const TABS: { key: Category; label: string }[] = [
  { key: "fraud_signal", label: "Fraud signals" },
  { key: "compliance", label: "Compliance" },
  { key: "completeness", label: "Completeness" },
];

export function AgentsStage(props: StageProps) {
  const { kase } = props;
  const [agent, setAgent] = useState<string | null>(null);
  const [tab, setTab] = useState<Category>("fraud_signal");
  const risky = kase.evidence.filter((e) => e.severity !== "info");
  const inScope = risky.filter((e) => agent === null || e.agent === agent);
  const count = (c: Category) => inScope.filter((e) => e.category === c).length;

  return (
    <>
      <Section title="Mandatory tools: rule sweep" aside="Run in parallel; select one to filter its findings">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">Agent</th>
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
      </Section>

      <Section title={agent ? `Findings from ${agent}` : "Findings"} aside={agent ? <button type="button" className="link" onClick={() => setAgent(null)}>Show all agents</button> : undefined}>
        <div className="segmented" role="tablist" aria-label="Finding category">
          {TABS.map(({ key, label }) => (
            <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>
              {label} <span className="count">{count(key)}</span>
            </button>
          ))}
        </div>
        <Findings {...props} evidence={inScope.filter((e) => e.category === tab)} empty="No findings in this category" />
      </Section>

      <AiTeam {...props} />
    </>
  );
}
