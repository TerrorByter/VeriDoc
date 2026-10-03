import type { ReviewPath, Routing } from "../types";

const PATH_LABEL: Record<ReviewPath, string> = {
  fast_track: "Fast track",
  standard_review: "Standard review",
  enhanced_review: "Enhanced review (EDD)",
};

export function RoutingBanner({ routing }: { routing: Routing }) {
  return (
    <section
      className={`card routing route-${routing.path}`}
      aria-labelledby="routing-title"
    >
      <div className="routing-head">
        <h2 id="routing-title">Route: {PATH_LABEL[routing.path]}</h2>
        <span className="muted">
          {routing.analyst_review ? "Analyst review required" : "No analyst review required"}
          {routing.qa_sampled && " · drawn for QA sample"} · policy {routing.policy_version}
        </span>
      </div>
      {routing.hard_flags.length > 0 && (
        <p className="hard-flags">
          Hard flags (override the score): {routing.hard_flags.join(", ")}
        </p>
      )}
      {routing.low_confidence.length > 0 && (
        <ul className="reasons">
          {routing.low_confidence.map((r) => (
            <li key={r}>Low confidence: {r}</li>
          ))}
        </ul>
      )}
      <ul className="reasons">
        {routing.reasons.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
    </section>
  );
}
