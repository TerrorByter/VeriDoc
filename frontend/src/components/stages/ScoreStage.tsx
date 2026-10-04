import type { StageProps } from "../workspace/StageDetail";
import { BAND_TONE, Pill } from "../ui";
import { Section } from "./common";

/** Band edges, as in the server configuration (BAND_LOW_MAX, BAND_MEDIUM_MAX) */
const LOW_MAX = 19;
const MEDIUM_MAX = 49;

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
    </>
  );
}
