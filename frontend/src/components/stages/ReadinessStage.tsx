import type { StageProps } from "../workspace/StageDetail";
import { Icon } from "../ui";
import { Section } from "./common";
import { Findings } from "./Findings";

const state = (status: string) =>
  status === "Provided" ? "done" : status === "Not checked in prototype" ? "pending" : "flag";

export function ReadinessStage(props: StageProps) {
  const { kase } = props;
  const findings = kase.evidence.filter((e) => e.category !== "fraud_signal" && e.severity !== "info");
  return (
    <>
      <Section title="Checklist" aside={kase.readiness?.status}>
        <ul className="checklist">
          {kase.readiness?.checklist.map((item) => (
            <li key={item.id} className={state(item.status)}>
              <Icon name={state(item.status)} />
              <span>{item.label}</span>
              <span className="muted small">{item.status}</span>
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Compliance and completeness findings" aside="Never added to the risk score">
        <Findings {...props} evidence={findings} empty="Nothing missing or invalid" />
      </Section>
    </>
  );
}
