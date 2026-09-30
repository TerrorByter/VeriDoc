import type { AgentReport } from "../types";

interface Props {
  agents: AgentReport[];
  extractionMode: string;
  docCount: number;
}

export function AgentPanel({ agents, extractionMode, docCount }: Props) {
  return (
    <section className="card" aria-labelledby="pipeline-title">
      <h2 id="pipeline-title">Analysis pipeline</h2>
      <ol className="pipeline">
        <li className="stage">
          <span className="stage-name">Intake</span>
          <span className="muted">{docCount} documents validated, hashed, classified</span>
        </li>
        <li className="stage">
          <span className="stage-name">Extraction</span>
          <span className="muted">{extractionMode === "llm" ? "Textract + Bedrock" : "Deterministic parser"}</span>
        </li>
        <li className="stage agents" aria-label="Agents run in parallel">
          <span className="stage-name">Agents <span className="muted">(parallel)</span></span>
          <ul className="agent-list">
            {agents.map((a) => (
              <li key={a.name} className="agent">
                <div className="agent-head">
                  <strong>{a.name}</strong>
                  <span className={`findings${a.findings ? " has" : ""}`}>
                    {a.findings} {a.findings === 1 ? "finding" : "findings"}
                  </span>
                </div>
                <p className="muted">{a.description}</p>
                <span className="fineprint">{a.checks_run} checks · {a.duration_ms} ms</span>
              </li>
            ))}
          </ul>
        </li>
        <li className="stage">
          <span className="stage-name">Scoring</span>
          <span className="muted">Rule-based, fraud signals only</span>
        </li>
        <li className="stage">
          <span className="stage-name">Officer review</span>
          <span className="muted">You decide; the system never does</span>
        </li>
      </ol>
    </section>
  );
}
