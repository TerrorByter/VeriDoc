import { APPLICATION_DOC } from "../types";
import type { InvestigationTask, Risk, ReviewPath, TaskPriority, ViewTarget } from "../types";
import { PATH_LABEL } from "./RoutingBanner";

const PRIORITY_LABEL: Record<TaskPriority, string> = {
  urgent: "Urgent · hard flag",
  high: "High",
  normal: "Normal",
};

/** What resolving the task would change, from the server's recalculation. */
function Change({ task, risk, path }: { task: InvestigationTask; risk: Risk; path: ReviewPath }) {
  if (task.category !== "fraud_signal") {
    return <p className="task-change muted">Resolve by requesting information; does not affect the score.</p>;
  }
  const routeMoves = task.path_if_resolved !== path;
  return (
    <p className="task-change">
      If resolved: score {risk.score} → <strong>{task.score_if_resolved}</strong> ({task.band_if_resolved})
      {routeMoves ? (
        <>, route → <strong>{PATH_LABEL[task.path_if_resolved]}</strong></>
      ) : (
        <span className="muted">, route unchanged</span>
      )}
    </p>
  );
}

export function InvestigationQueue({
  tasks,
  risk,
  path,
  openFlags,
  onView,
  pageOf,
}: {
  tasks: InvestigationTask[];
  risk: Risk;
  path: ReviewPath;
  openFlags: string[];
  onView: (target: ViewTarget) => void;
  /** Page of a finding, by evidence ID */
  pageOf: (evidenceId: string) => number;
}) {
  if (tasks.length === 0) return null;
  const open = new Set(openFlags);
  const decided = (t: InvestigationTask) =>
    t.category === "fraud_signal" && t.evidence_ids.every((id) => !open.has(id));
  const remaining = tasks.filter((t) => !decided(t)).length;

  return (
    <section className="card" aria-labelledby="queue-title">
      <div className="routing-head">
        <h2 id="queue-title">Investigation queue</h2>
        <span className="muted">
          {remaining} of {tasks.length} open
        </span>
      </div>
      <p className="muted">
        What to resolve, in order: hard flags first, then the findings that add the most risk.
        Each task says how to resolve it and what that would change.
      </p>
      <ol className="queue">
        {tasks.map((t) => (
          <li key={t.task_id} className={`task prio-${t.priority}${decided(t) ? " done" : ""}`}>
            <div className="ev-head">
              <span className="prio">{PRIORITY_LABEL[t.priority]}</span>
              <strong>{t.name}</strong>
              <span className="muted">
                {t.signal_id} · {t.document} · {t.evidence_ids.join(", ")}
                {t.points > 0 && ` · +${t.points} pts`}
              </span>
              {decided(t) && <span className="status ok">decided</span>}
            </div>
            <p className="task-remedy">{t.remedy}</p>
            <Change task={t} risk={risk} path={path} />
            {t.document !== APPLICATION_DOC && (
              <button
                type="button"
                className="link"
                onClick={() =>
                  onView({ docId: t.document, page: pageOf(t.evidence_ids[0]), evidenceId: t.evidence_ids[0] })
                }
              >
                View in document →
              </button>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
