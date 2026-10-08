import { useState } from "react";
import { AGENT_TITLE, AGENTS } from "../../../catalogue";
import type { AgentName, ToolCall, ViewTarget } from "../../../types";
import { Empty, Pill, tabKeys } from "../../ui";

const argText = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value));

/** A page-text call opens the page it read in the document pane. */
function pageTarget(call: ToolCall): ViewTarget | null {
  const { doc_id: docId, page } = call.args;
  return call.tool === "get_page_text" && typeof docId === "string" && typeof page === "number"
    ? { docId, page }
    : null;
}

/** Every tool call the team made, one tab per agent. Each row is addressable as call-R01, so a lead's citations
 * can jump to it. A call the checker refused (the agent's own mistake, which it was told about and could correct)
 * is marked Refused, not as a failure. */
export function Trace({ calls, agent, focus, onAgent, onView }: {
  calls: ToolCall[];
  agent: AgentName;
  /** The call a lead citation last jumped to */
  focus: string | null;
  onAgent: (agent: AgentName) => void;
  onView: (target: ViewTarget) => void;
}) {
  const [hideRefused, setHideRefused] = useState(false);
  const mine = calls.filter((c) => c.agent === agent);
  const refused = mine.filter((c) => c.error).length;
  const shown = hideRefused ? mine.filter((c) => !c.error) : mine;
  return (
    <>
      <div className="segmented trace-tabs" role="tablist" aria-label="Agent" onKeyDown={tabKeys(AGENTS, agent, onAgent)}>
        {AGENTS.map((a) => (
          <button
            key={a}
            id={`trace-tab-${a}`}
            type="button"
            role="tab"
            aria-selected={a === agent}
            aria-controls="trace-panel"
            tabIndex={a === agent ? 0 : -1}
            onClick={() => onAgent(a)}
          >
            {AGENT_TITLE[a]} <span className="count">{calls.filter((c) => c.agent === a).length}</span>
          </button>
        ))}
      </div>
      <div id="trace-panel" role="tabpanel" aria-labelledby={`trace-tab-${agent}`}>
        {refused > 0 && (
          <label className="check-row small">
            <input type="checkbox" checked={hideRefused} onChange={(e) => setHideRefused(e.target.checked)} />
            Hide {refused} refused {refused === 1 ? "call" : "calls"}
            <span className="muted">(the checker told the agent why and it could correct them)</span>
          </label>
        )}
        {shown.length === 0 ? (
          <Empty title="No tool calls">{AGENT_TITLE[agent]} called no tools on this case.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <caption className="sr-only">Tool calls made by the {AGENT_TITLE[agent]}</caption>
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
                        {c.error && (
                          <Pill tone="neutral" title="The checker refused this call and told the agent why">Refused</Pill>
                        )}{" "}
                        {c.summary ? c.summary.replace(/^error:\s*/, "") : <span className="muted">—</span>}
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
