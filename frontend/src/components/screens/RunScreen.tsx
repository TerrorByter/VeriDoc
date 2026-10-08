import { useEffect, useState } from "react";
import { getProgress } from "../../api";
import { STAGES, stageOf, type StageInfo } from "../../catalogue";
import { DEFAULT_STAGE, href, navigate } from "../../route";
import type { Progress, StageId, StageState } from "../../types";
import { ErrorNote, Icon, ServiceTag } from "../ui";

const POLL_MS = 500;
/** Pause on the finished list before opening the case, so the last tick is seen */
const OPEN_AFTER_MS = 700;

/** "partial": finished, but its AI team step failed (the run carries on without it) */
type RowState = StageState | "pending" | "partial";

const ROW_LABEL: Record<RowState, string> = {
  running: "Running", ran: "Done", off: "Switched off", failed: "Failed", pending: "Waiting",
  partial: "Finished, AI team failed",
};

function useElapsed(running: boolean): number {
  const [start] = useState(() => Date.now());
  const [now, setNow] = useState(start);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [running]);
  return Math.floor((now - start) / 1000);
}

const clock = (s: number) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

export function RunScreen({ caseId }: { caseId: string }) {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const running = !error && progress?.state !== "done" && progress?.state !== "failed";
  const elapsed = useElapsed(running);

  useEffect(() => {
    let timer = 0;
    let cancelled = false;
    const poll = () =>
      getProgress(caseId)
        .then((p) => {
          if (cancelled) return;
          setProgress(p);
          if (p.state === "running") timer = window.setTimeout(poll, POLL_MS);
          if (p.state === "done") {
            timer = window.setTimeout(
              () => navigate({ screen: "case", caseId, stage: DEFAULT_STAGE }), OPEN_AFTER_MS,
            );
          }
        })
        .catch((e: Error) => !cancelled && setError(e.message));
    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [caseId]);

  const states = new Map(progress?.stages.map((s) => [s.stage_id, s.state]));
  const stateOf = (id: StageId): RowState => {
    const st = states.get(id) ?? "pending";
    return st === "running" && progress?.state === "failed" ? "failed" : st;
  };
  // The AI team and the shadow scorer failing never stop the run: their rows still finish, flagged but not as a failure
  const isAi = (id: StageId) => id === "ai_team" || id === "shadow_score";
  const aiFailed = (id: StageId) => isAi(id) && states.get(id) === "failed";
  /** A row's state from its steps: stage 5 runs the rule agents and the AI team, stage 6 the score and the shadow scorer */
  const rowOf = (s: StageInfo): RowState => {
    const ids = s.steps ?? [s.id];
    const steps = ids.map((id) => (aiFailed(id) ? "ran" : stateOf(id)));
    if (steps.includes("failed")) return "failed";
    if (steps.includes("running")) return "running";
    if (steps.every((st) => st === "off")) return "off";
    if (!steps.every((st) => st === "ran" || st === "off")) return "pending";
    return ids.some(aiFailed) ? "partial" : "ran";
  };
  const finished = STAGES.filter((s) => ["ran", "off", "partial"].includes(rowOf(s))).length;
  const current = progress?.current ? stageOf(progress.current) : undefined;

  return (
    <main className="page narrow">
      <div className="page-head">
        <div>
          <p className="muted">{progress?.state === "done" ? "Finished" : progress?.state === "failed" ? "Stopped" : "Running"}</p>
          <h1 className="mono case-id">{caseId}</h1>
        </div>
        <span className="mono muted elapsed" aria-label="Time elapsed">{clock(elapsed)}</span>
      </div>

      <div
        className="progress"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={STAGES.length}
        aria-valuenow={finished}
        aria-label="Pipeline progress"
      >
        <i style={{ width: `${(finished / STAGES.length) * 100}%` }} />
      </div>
      <p className="muted" aria-live="polite">
        {current
          ? `Stage ${current.n} of ${STAGES.length}: ${current.name}`
          : progress?.state === "done"
            ? "All stages finished. Opening the case…"
            : progress?.state === "failed"
              ? "The run stopped."
              : "Starting…"}
      </p>

      {(error || progress?.error) && (
        <ErrorNote>
          {(progress?.error ?? error ?? "").replace(/\.$/, "")}. <a href={href({ screen: "new" })}>Start a new case</a>
        </ErrorNote>
      )}

      <ol className="run-list panel">
        {STAGES.map((s) => {
          const st = rowOf(s);
          const team = s.steps?.includes("ai_team");
          return (
            <li key={s.id} className={`run-row ${st}`} aria-current={st === "running" ? "step" : undefined}>
              <span className="run-icon">
                {st === "running" ? (
                  <span className="spinner" aria-label="Running" />
                ) : (
                  <Icon name={st === "ran" ? "done" : st === "off" ? "off" : st === "failed" || st === "partial" ? "flag" : "pending"} />
                )}
              </span>
              <span className="run-name">
                <span className="step-n">{s.n}</span> {s.name}
              </span>
              <span>{s.service && st !== "off" && <ServiceTag service={s.service} />}</span>
              <span className="run-state">{ROW_LABEL[st]}</span>
              {team && stateOf("ai_team") === "running" && progress?.detail && (
                <span className="run-detail muted small">{progress.detail}</span>
              )}
            </li>
          );
        })}
      </ol>
      <p className="muted small">
        Stages switched off in the server configuration are shown, so you can see what did not run.
      </p>
    </main>
  );
}
