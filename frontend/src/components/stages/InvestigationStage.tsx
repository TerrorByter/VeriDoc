import { useState } from "react";
import type { StageProps } from "../workspace/StageDetail";
import { Chains } from "./investigation/Chains";
import { Queue } from "./investigation/Queue";
import { Timeline } from "./investigation/Timeline";

type View = "queue" | "chains" | "timeline";

export function InvestigationStage(props: StageProps) {
  const { kase } = props;
  const [view, setView] = useState<View>("queue");
  const broken = kase.chains.filter((c) => c.broken).length;
  const anomalies = kase.timeline.filter((e) => e.evidence_ids.length > 0).length;
  const tabs: { key: View; label: string; count: number; hot: boolean }[] = [
    { key: "queue", label: "Investigation queue", count: kase.investigation.length, hot: kase.investigation.length > 0 },
    { key: "chains", label: "Transaction chains", count: kase.chains.length, hot: broken > 0 },
    { key: "timeline", label: "Timeline", count: kase.timeline.length, hot: anomalies > 0 },
  ];
  return (
    <>
      <div className="segmented" role="tablist" aria-label="Investigation view">
        {tabs.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={view === t.key} onClick={() => setView(t.key)}>
            {t.label} <span className={`count${t.hot ? " hot" : ""}`}>{t.count}</span>
          </button>
        ))}
      </div>
      {view === "queue" && <Queue {...props} />}
      {view === "chains" && <Chains {...props} />}
      {view === "timeline" && <Timeline {...props} />}
    </>
  );
}
