import { AGENT_TITLE, AGENTS } from "../../../catalogue";
import type { AgentName, ToolCall, ViewTarget } from "../../../types";
import { Empty, Pill } from "../../ui";

const argText = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value));

/** A page-text call opens the page it read in the document pane. */
function pageTarget(call: ToolCall): ViewTarget | null {
  const { doc_id: docId, page } = call.args;
  return call.tool === "get_page_text" && typeof docId === "string" && typeof page === "number"
    ? { docId, page }
    : null;
}

/** Every tool call the team made, one tab per agent. Each row is addressable
 * as call-R01, so a lead's citations can jump to it. */
export function Trace({ calls, agent, focus, onAgent, onView }: {
  calls: ToolCall[];
  agent: AgentName;
  /** The call a lead citation last jumped to */
  focus: string | null;
  onAgent: (agent: AgentName) => void;
  onView: (target: ViewTarget) => void;
}) {
  const shown = calls.filter((c) => c.agent === agent);
  return (
    <>
      <div className="segmented trace-tabs" role="tablist" aria-label="Agent">
        {AGENTS.map((a) => (
          <button
            key={a}
            id={`trace-tab-${a}`}
            type="button"
            role="tab"
            aria-selected={a === agent}
            aria-controls="trace-panel"
            onClick={() => onAgent(a)}
          >
            {AGENT_TITLE[a]} <span className="count">{calls.filter((c) => c.agent === a).length}</span>
          </button>
        ))}
      </div>
      <div id="trace-panel" role="tabpanel" aria-labelledby={`trace-tab-${agent}`}>
        {shown.length === 0 ? (
          <Empty title="No tool calls">{AGENT_TITLE[agent]} called no tools on this case.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Call</th>
                  <th scope="col">Tool</th>
                  <th scope="col">Arguments</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((c) => {
                  const args = Object.entries(c.args).map(([k, v]) => `${k}=${argText(v)}`).join(", ") || "—";
                  const target = pageTarget(c);
                  return (
                    <tr key={c.call_id} id={`call-${c.call_id}`} className={c.call_id === focus ? "selected" : undefined}>
                      <td className="mono muted">{c.call_id}</td>
                      <td className="mono">{c.tool}</td>
                      <td className="trace-args">
                        {target ? (
                          <button type="button" className="link mono" title="Show this page" onClick={() => onView(target)}>
                            {args}
                          </button>
                        ) : (
                          <span className="mono">{args}</span>
                        )}
                      </td>
                      <td>
                        {c.error && <Pill tone="crit">Error</Pill>} {c.summary || <span className="muted">—</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
