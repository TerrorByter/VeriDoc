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
      {risk.reasons.length > 0 && (
        <ul className="reasons">
          {risk.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
      <p className="fineprint">Rule-based score. No AI model sets or changes it.</p>
    </section>
  );
}
