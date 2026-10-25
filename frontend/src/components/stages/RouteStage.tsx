import { PATH_LABEL } from "../../catalogue";
import type { ReviewPath } from "../../types";
import type { StageProps } from "../workspace/StageDetail";
import { Empty } from "../ui";
import { Section } from "./common";

const PATHS: { path: ReviewPath; meaning: string }[] = [
  { path: "fast_track", meaning: "Low risk and complete: a light-touch check" },
  { path: "standard_review", meaning: "An analyst reviews every finding" },
  { path: "enhanced_review", meaning: "Enhanced due diligence before any decision" },
];

export function RouteStage({ kase }: StageProps) {
  const routing = kase.routing;
  if (!routing) return <Empty title="This case was stored before routing existed" />;
  return (
    <>
      <ol className="path-ladder" aria-label="Review paths, lightest first">
        {PATHS.map(({ path, meaning }) => (
          <li key={path} className={path === routing.path ? "chosen" : ""} aria-current={path === routing.path ? "true" : undefined}>
            <strong>{PATH_LABEL[path]}</strong>
            <span className="muted small">{meaning}</span>
          </li>
        ))}
      </ol>

      <Section title="How the policy decided" aside={<>Policy <span className="mono">{routing.policy_version}</span></>}>
        <ol className="reasons">
          {routing.reasons.map((r) => <li key={r}>{r}</li>)}
          {routing.low_confidence.map((r) => <li key={r}>Low confidence: {r}</li>)}
        </ol>
      </Section>

      <dl className="facts">
        <div><dt>Hard flags</dt><dd>{routing.hard_flags.length ? routing.hard_flags.join(", ") : "None"}</dd></div>
        <div><dt>Analyst review</dt><dd>{routing.analyst_review ? "Required" : "Not required"}</dd></div>
        <div><dt>Enhanced due diligence</dt><dd>{routing.edd_required ? "Required: the MLRO must review the case before it is approved" : "Not required"}</dd></div>
        <div><dt>Quality sample</dt><dd>{routing.qa_sampled ? "Drawn for QA" : "Not drawn"}</dd></div>
      </dl>
    </>
  );
}
