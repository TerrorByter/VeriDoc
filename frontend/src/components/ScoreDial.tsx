import type { Risk } from "../types";

const RADIUS = 54;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function ScoreDial({ risk }: { risk: Risk }) {
  const offset = CIRCUMFERENCE * (1 - risk.score / 100);
  return (
    <section className={`card dial band-${risk.band.toLowerCase()}`} aria-labelledby="risk-title">
      <h2 id="risk-title">Fraud risk</h2>
      <div className="dial-body">
        <svg viewBox="0 0 140 140" role="img" aria-label={`Risk score ${risk.score} out of 100, ${risk.band}`}>
          <circle className="track" cx="70" cy="70" r={RADIUS} />
          <circle
            className="value"
            cx="70"
            cy="70"
            r={RADIUS}
            strokeDasharray={CIRCUMFERENCE}
            strokeDashoffset={offset}
            transform="rotate(-90 70 70)"
          />
          <text x="70" y="68" className="num">{risk.score}</text>
          <text x="70" y="90" className="sub">/ 100</text>
        </svg>
        <div>
          <span className="band-pill">{risk.band} risk</span>
          {risk.manual_review_required && (
            <p className="flag-note">Manual review required</p>
          )}
        </div>
      </div>
      {risk.contributions.length > 0 && (
        <>
          <h3 className="why">Why this score?</h3>
          <table className="contributions">
            <tbody>
              {risk.contributions.map((c) => (
                <tr key={c.evidence_id}>
                  <td className="pts">+{c.points}</td>
                  <td>
                    {c.name}
                    <span className="muted"> · {c.evidence_id}, {c.document} p.{c.page}</span>
                  </td>
                  <td className="sig">{c.signal_id}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td className="pts">{risk.contributions.reduce((n, c) => n + c.points, 0)}</td>
                <td colSpan={2} className="muted">
                  total{risk.score === 100 ? ", capped at 100" : ""}
                </td>
              </tr>
            </tfoot>
          </table>
        </>
      )}
      {risk.reasons
        .filter((r) => r.startsWith("Hard rule"))
        .map((r) => (
          <p key={r} className="flag-note">{r}</p>
        ))}
      <p className="fineprint">
        Rule-based score (rules {risk.rule_version}). No AI model sets or changes it.
      </p>
    </section>
  );
}
