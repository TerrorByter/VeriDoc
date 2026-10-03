import type { ReviewPoint, SemanticReview } from "../types";

function Points({ points }: { points: ReviewPoint[] }) {
  return (
    <ul className="review-points">
      {points.map((p) => (
        <li key={p.text}>
          {p.text} <span className="cite">{p.evidence_ids.join(", ")}</span>
        </li>
      ))}
    </ul>
  );
}

export function SemanticReviewPanel({ review }: { review: SemanticReview }) {
  return (
    <section className="card review" aria-labelledby="review-title">
      <div className="routing-head">
        <h2 id="review-title">Semantic review</h2>
        <span className="muted">
          {review.source === "llm" ? "AI synthesis of verified findings" : "Rule-based synthesis"}
          {review.rejected > 0 && ` · ${review.rejected} unsupported point(s) withheld`}
        </span>
      </div>
      <p className="muted">
        Does the applicant&apos;s story hold together? Every point cites the findings it relies on;
        it does not score, route or decide.
      </p>
      <h3>Observations</h3>
      <Points points={review.observations} />
      {review.questions.length > 0 && (
        <>
          <h3>Questions for the applicant</h3>
          <Points points={review.questions} />
        </>
      )}
    </section>
  );
}
