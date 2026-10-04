import { PATH_LABEL } from "../../catalogue";
import type { StageProps } from "../workspace/StageDetail";
import { BAND_TONE, PATH_TONE, Pill } from "../ui";
import { Section } from "./common";

/** Band edges, as in the server configuration (BAND_LOW_MAX, BAND_MEDIUM_MAX) */
const LOW_MAX = 19;
const MEDIUM_MAX = 49;

/** The shadow scorer's proposed route and the route the gate would take, beside the rule route: an experiment
 * that is logged and never used. */
function ShadowCard({ kase }: Pick<StageProps, "kase">) {
  const { shadow, gate, routing } = kase;
  if (!shadow && !gate) return null;
  return (
    <Section title="Shadow score (experiment, not used)" aside="Logged beside the rule route; nothing reads it">
      <dl className="facts">
        <div>
          <dt>Rule route (champion)</dt>
          <dd>{routing ? <Pill tone={PATH_TONE[routing.path]}>{PATH_LABEL[routing.path]}</Pill> : "—"}</dd>
        </div>
        <div>
          <dt>Shadow scorer proposes</dt>
          <dd>
            {shadow?.status === "ran" && shadow.route ? (
              <>
                <Pill tone={PATH_TONE[shadow.route]}>{PATH_LABEL[shadow.route]}</Pill>{" "}
                <span className="muted small">{shadow.agrees_with_champion ? "agrees" : "disagrees"}</span>
              </>
            ) : shadow ? (
              <span className="muted">Failed: {shadow.error}</span>
            ) : (
              <span className="muted">Not run</span>
            )}
          </dd>
        </div>
        {gate && (
          <div>
            <dt>Gate would route</dt>
            <dd><Pill tone={PATH_TONE[gate.would_route]}>{PATH_LABEL[gate.would_route]}</Pill></dd>
          </div>
        )}
      </dl>
      {shadow?.status === "ran" && (
        <ul className="reasons muted small">
          {shadow.reasons.map((r, i) => (
            <li key={i}><span className="mono">{r.code}</span>{r.evidence_ids.length > 0 && <> on <span className="mono">{r.evidence_ids.join(", ")}</span></>}</li>
          ))}
        </ul>
      )}
      {gate && <ul className="reasons muted small">{gate.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
      {shadow && <p className="muted small">Model <span className="mono">{shadow.model}</span>, prompt <span className="mono">{shadow.prompt_version}</span>.</p>}
    </Section>
  );
}

export function ScoreStage({ kase, onView }: StageProps) {
  const { risk } = kase;
  const total = risk.contributions.reduce((n, c) => n + c.points, 0);
  return (
    <>
      <div className="gauge-card">
        <div className="gauge-top">
          <span className="gauge-score mono">{risk.score}</span>
          <span className="muted">/ 100</span>
          <Pill tone={BAND_TONE[risk.band]}>{risk.band} risk</Pill>
        </div>
        <div className="gauge" role="img" aria-label={`Score ${risk.score} of 100, ${risk.band} band`}>
          <span className="zone low" style={{ width: `${LOW_MAX + 1}%` }}>Low</span>
          <span className="zone med" style={{ width: `${MEDIUM_MAX - LOW_MAX}%` }}>Medium</span>
          <span className="zone high" style={{ width: `${100 - MEDIUM_MAX - 1}%` }}>High</span>
          <span className="needle" style={{ left: `${risk.score}%` }} />
        </div>
        <p className="muted small">
          Low up to {LOW_MAX}, medium up to {MEDIUM_MAX}, high from {MEDIUM_MAX + 1}. Rule set{" "}
          <span className="mono">{risk.rule_version}</span>. No model sets or changes the score.
        </p>
      </div>

      <Section title="Why this score" aside="Every point names its signal and finding">
        {risk.contributions.length === 0 ? (
          <p className="muted">No fraud signals: nothing adds to the score.</p>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Signal</th>
                  <th scope="col">Finding</th>
                  <th scope="col">Where</th>
                  <th scope="col" className="num">Points</th>
                </tr>
              </thead>
              <tbody>
                {risk.contributions.map((c) => (
                  <tr key={c.evidence_id}>
                    <td className="mono">{c.signal_id}</td>
                    <td>{c.name} <span className="muted mono small">{c.evidence_id}</span></td>
                    <td>
                      {c.document === "pack" ? (
                        <span className="muted">Application data</span>
                      ) : (
                        <button type="button" className="link mono" onClick={() => onView({ docId: c.document, page: c.page, evidenceId: c.evidence_id })}>
                          {c.document}, p.{c.page}
                        </button>
                      )}
                    </td>
                    <td className="num mono">+{c.points}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={3}>Total{risk.score < total ? ", capped at 100" : ""}</td>
                  <td className="num mono">{risk.score}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </Section>

      <ShadowCard kase={kase} />
    </>
  );
}
