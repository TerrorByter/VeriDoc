import { STAGES, type StageGroup } from "../../catalogue";
import { href } from "../../route";
import type { AuditEvent, CaseView, RailStageId } from "../../types";
import { Icon } from "../ui";
import { stageStatus } from "./stageStatus";

export function StageRail({ kase, audit, current }: { kase: CaseView; audit: AuditEvent[]; current: RailStageId }) {
  let group: StageGroup | null = null;
  return (
    <nav className="rail" aria-label="Pipeline stages">
      <ol>
        {STAGES.map((s) => {
          const status = stageStatus(kase, audit, s.id);
          const heading = s.group !== group ? s.group : null;
          group = s.group;
          return (
            <li key={s.id}>
              {heading && <span className="rail-group">{heading}</span>}
              <a
                className={`step ${status.state}`}
                href={href({ screen: "case", caseId: kase.case_id, stage: s.id })}
                aria-current={s.id === current ? "step" : undefined}
              >
                <Icon name={status.state === "flag" ? "flag" : status.state === "off" ? "off" : "done"} />
                <span className="step-label">
                  <span className="step-n">{s.n}</span>
                  {s.name}
                </span>
                {status.state === "off" ? (
                  <span className="count">off</span>
                ) : (
                  status.count !== undefined && (
                    <span className={`count${status.state === "flag" ? " hot" : ""}`}>{status.count}</span>
                  )
                )}
              </a>
            </li>
          );
        })}
      </ol>
      <p className="rail-hint muted">
        <kbd>←</kbd> <kbd>→</kbd> step through stages
      </p>
    </nav>
  );
}
