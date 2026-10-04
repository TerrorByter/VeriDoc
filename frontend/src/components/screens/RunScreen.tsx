import { useEffect, useState } from "react";
import { getProgress } from "../../api";
import { STAGES, stageOf, type StageInfo } from "../../catalogue";
import { DEFAULT_STAGE, href, navigate } from "../../route";
import type { Progress, StageId, StageState } from "../../types";
import { ErrorNote, Icon, ServiceTag } from "../ui";

const POLL_MS = 500;
/** Pause on the finished list before opening the case, so the last tick is seen */
const OPEN_AFTER_MS = 700;

type RowState = StageState | "pending";

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
  // The AI team failing never stops the run: its row still counts as finished
  const teamFailed = states.get("ai_team") === "failed";
  /** A row's state from its steps: stage 5 runs both the rule agents and the AI team */
  const rowOf = (s: StageInfo): RowState => {
    const steps = (s.steps ?? [s.id]).map(stateOf);
    if (steps.includes("failed")) return "failed";
    if (steps.includes("running")) return "running";
    if (steps.every((st) => st === "off")) return "off";
    return steps.every((st) => st === "ran" || st === "off") ? "ran" : "pending";
  };
  const finished = STAGES.filter((s) => {
    const st = rowOf(s);
    return st === "ran" || st === "off" || (st === "failed" && teamFailed && s.steps?.includes("ai_team"));
  }).length;
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
                  <Icon name={st === "ran" ? "done" : st === "off" ? "off" : st === "failed" ? "flag" : "pending"} />
                )}
              </span>
              <span className="run-name">
                <span className="step-n">{s.n}</span> {s.name}
              </span>
              <span>{s.service && st !== "off" && <ServiceTag service={s.service} />}</span>
              <span className="run-state">
                {st === "failed" && team && teamFailed
                  ? "Finished, AI team failed"
                  : { running: "Running", ran: "Done", off: "Switched off", failed: "Failed", pending: "Waiting" }[st]}
              </span>
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
