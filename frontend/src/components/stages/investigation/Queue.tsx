import { PATH_LABEL } from "../../../catalogue";
import { APPLICATION_DOC } from "../../../types";
import type { InvestigationTask, TaskPriority } from "../../../types";
import type { StageProps } from "../../workspace/StageDetail";
import { Empty, Pill, type Tone } from "../../ui";

const PRIORITY: Record<TaskPriority, { label: string; tone: Tone }> = {
  urgent: { label: "Urgent", tone: "crit" },
  high: { label: "High", tone: "high" },
  normal: { label: "Normal", tone: "neutral" },
};

export function Queue({ kase, onView }: StageProps) {
  const tasks = kase.investigation;
  if (tasks.length === 0) return <Empty title="Nothing to investigate">No finding needs follow-up.</Empty>;
  const open = new Set(kase.open_flags);
  const decided = (t: InvestigationTask) =>
    t.category === "fraud_signal" && t.evidence_ids.every((id) => !open.has(id));
  const pageOf = (id: string) => kase.evidence.find((e) => e.evidence_id === id)?.page ?? 1;
  const path = kase.routing?.path;

  return (
    <>
      <p className="muted">
        What to resolve, in order: hard flags first, then the findings adding the most risk. Each task
        shows what resolving it would change, recalculated by the same rules.
      </p>
      <ol className="tasks">
        {tasks.map((t) => (
          <li key={t.task_id} className={`task${decided(t) ? " done" : ""}`}>
            <div className="task-top">
              <span className="mono muted">{t.task_id}</span>
              <strong>{t.name}</strong>
              <Pill tone={PRIORITY[t.priority].tone}>{PRIORITY[t.priority].label}</Pill>
              {decided(t) && <Pill tone="low">Decided</Pill>}
              <span className="spacer" />
              {t.document !== APPLICATION_DOC && (
                <button
                  type="button"
                  className="link"
                  onClick={() => onView({ docId: t.document, page: pageOf(t.evidence_ids[0]), evidenceId: t.evidence_ids[0] })}
                >
                  Show in document
                </button>
              )}
            </div>
            <p>{t.remedy}</p>
            {t.category === "fraud_signal" ? (
              <p className="what-if">
                If resolved: score {kase.risk.score} to <strong>{t.score_if_resolved}</strong> ({t.band_if_resolved})
                {path && t.path_if_resolved !== path ? (
                  <>, route to <strong>{PATH_LABEL[t.path_if_resolved]}</strong></>
                ) : (
                  <span className="muted">, route unchanged</span>
                )}
              </p>
            ) : (
              <p className="what-if muted">Resolved by requesting information; the score does not change.</p>
            )}
            <p className="muted small mono">{t.signal_id} · {t.evidence_ids.join(", ")}{t.points > 0 && ` · +${t.points} points`}</p>
          </li>
        ))}
      </ol>
    </>
  );
}
