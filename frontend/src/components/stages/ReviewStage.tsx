import type { ReviewPoint } from "../../types";
import type { StageProps } from "../workspace/StageDetail";
import { Empty } from "../ui";
import { OffNote, Section } from "./common";

function Points({ points }: { points: ReviewPoint[] }) {
  return (
    <ul className="review-points">
      {points.map((p) => (
        <li key={p.text}>
          <span>{p.text}</span>
          <span className="mono small muted">{p.evidence_ids.join(", ")}</span>
        </li>
      ))}
    </ul>
  );
}

export function ReviewStage({ kase }: StageProps) {
  const { review } = kase;
  if (kase.stages.find((s) => s.stage_id === "semantic_review")?.status === "off") {
    return <OffNote stage="semantic_review">The investigation queue and findings still give the full picture.</OffNote>;
  }
  if (!review) return <Empty title="Nothing to review">The case has no findings, so the model was not called.</Empty>;
  return (
    <>
      <div className="callout">
        <p>
          {review.source === "llm"
            ? "Written by Claude from the verified findings, then checked: every point must cite real findings and use only their numbers."
            : "The model's answer did not pass the checks, so this summary was built from rules instead."}
          {review.rejected > 0 && ` ${review.rejected} unsupported point${review.rejected === 1 ? " was" : "s were"} withheld.`}
        </p>
      </div>
      <Section title="Observations">
        <Points points={review.observations} />
      </Section>
      {review.questions.length > 0 && (
        <Section title="Questions for the applicant">
          <Points points={review.questions} />
        </Section>
      )}
    </>
  );
}
