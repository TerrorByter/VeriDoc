import { useState } from "react";
import { duration } from "../../catalogue";
import type { Category } from "../../types";
import type { StageProps } from "../workspace/StageDetail";
import { explainedFindings } from "../workspace/stageStatus";
import { tabKeys } from "../ui";
import { Fold, Section } from "./common";
import { Findings } from "./Findings";

const TABS: { key: Category; label: string }[] = [
  { key: "fraud_signal", label: "Fraud signals" },
  { key: "compliance", label: "Compliance" },
  { key: "completeness", label: "Completeness" },
];
const TAB_KEYS = TABS.map((t) => t.key);

export function RuleChecksStage(props: StageProps) {
  const { kase } = props;
  const [group, setGroup] = useState<string | null>(null);
  const [tab, setTab] = useState<Category>("fraud_signal");
  const explained = explainedFindings(kase);
  const risky = kase.evidence.filter((e) => e.severity !== "info");
  const inScope = risky.filter((e) => group === null || e.rule_group === group);
  // A finding code explained is not an open one: the tab counts show what still needs the analyst
  const count = (c: Category) => inScope.filter((e) => e.category === c && !(e.evidence_id in explained)).length;

  return (
    <>
      <Section
        title={group ? `Rule findings from ${group}` : "Rule findings"}
        aside={group ? <button type="button" className="link" onClick={() => setGroup(null)}>Show all rule groups</button> : "From the rule checks, before any AI"}
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

      <Fold title="Rule groups" aside="Run in parallel; select a rule group to filter the findings above">
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
              {kase.rule_group_reports.map((g) => (
                <tr
                  key={g.name}
                  className={`clickable${group === g.name ? " selected" : ""}`}
                  onClick={() => setGroup(group === g.name ? null : g.name)}
                >
                  <td>
                    <button type="button" className="row-button" aria-pressed={group === g.name} title={g.description}>
                      {g.name}
                    </button>
                    <span className="muted small block">{g.description}</span>
                  </td>
                  <td className="num">{g.checks_run}</td>
                  <td className="num">
                    <span className={`count${g.findings ? " hot" : ""}`}>{g.findings}</span>
                  </td>
                  <td className="num mono muted">{duration(g.duration_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Fold>
    </>
  );
}
