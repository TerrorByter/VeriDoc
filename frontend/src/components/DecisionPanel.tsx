import { useState } from "react";
import type { AnalystDecision, CaseStatus, MlroDecision } from "../types";

const ANALYST: { key: AnalystDecision; label: string; tone: string }[] = [
  { key: "approve", label: "Approve", tone: "primary" },
  { key: "request_info", label: "Request more info", tone: "ghost" },
  { key: "escalate", label: "Escalate to MLRO", tone: "ghost" },
  { key: "decline", label: "Decline", tone: "danger" },
];

const STATUS_LABEL: Record<CaseStatus, string> = {
  assessed: "Awaiting analyst decision",
  info_requested: "Waiting for documents from the applicant",
  escalated: "Escalated: waiting for the MLRO",
  mlro_reviewed: "MLRO has decided: awaiting analyst's final decision",
  approved: "Approved",
  declined: "Declined",
};

interface Props {
  status: CaseStatus;
  allowedActions: string[];
  openFlags: string[];
  onAnalyst: (decision: AnalystDecision, reason: string, actor: string) => Promise<void>;
  onMlro: (decision: MlroDecision, reason: string, actor: string) => Promise<void>;
  onDocuments: (files: File[]) => Promise<void>;
}

export function DecisionPanel({
  status,
  allowedActions,
  openFlags,
  onAnalyst,
  onMlro,
  onDocuments,
}: Props) {
  const [reason, setReason] = useState("");
  const [actor, setActor] = useState("analyst");
  const [files, setFiles] = useState<File[]>([]);
  const ready = reason.trim() !== "" && actor.trim() !== "";
  const analystActions = ANALYST.filter((a) => allowedActions.includes(a.key));
  const mlroTurn = allowedActions.includes("mlro_decision");

  const submit = async (work: () => Promise<void>) => {
    await work();
    setReason("");
  };

  return (
    <section className="card" aria-labelledby="decision-title">
      <h2 id="decision-title">Decision</h2>
      <p className={`status ${allowedActions.length ? "warn" : "ok"}`} role="status">
        {STATUS_LABEL[status]}
      </p>
      <p className="muted">
        The system never approves, declines or reports a case. Every decision is a
        person's and is recorded in the audit log.
      </p>

      {allowedActions.includes("documents_added") && (
        <div className="row">
          <input
            type="file"
            multiple
            accept="application/pdf"
            aria-label="Requested documents"
            onChange={(e) => setFiles([...(e.target.files ?? [])])}
          />
          <button
            type="button"
            className="btn primary"
            disabled={files.length === 0}
            onClick={() => onDocuments(files).then(() => setFiles([]))}
          >
            Add documents and re-assess
          </button>
        </div>
      )}

      {(analystActions.length > 0 || mlroTurn) && (
        <>
          <div className="row">
            <input
              type="text"
              value={actor}
              aria-label={mlroTurn ? "MLRO name" : "Analyst name"}
              placeholder={mlroTurn ? "MLRO name" : "Analyst name"}
              onChange={(e) => setActor(e.target.value)}
            />
          </div>
          <textarea
            value={reason}
            rows={3}
            placeholder="Reason (required)"
            aria-label="Reason for decision"
            onChange={(e) => setReason(e.target.value)}
          />
        </>
      )}

      {analystActions.length > 0 && (
        <div className="row">
          {analystActions.map(({ key, label, tone }) => {
            const blocked = key === "approve" && openFlags.length > 0;
            return (
              <button
                key={key}
                type="button"
                className={`btn ${tone}`}
                disabled={!ready || blocked}
                title={blocked ? `Decide flags first: ${openFlags.join(", ")}` : undefined}
                onClick={() => submit(() => onAnalyst(key, reason.trim(), actor.trim()))}
              >
                {label}
              </button>
            );
          })}
        </div>
      )}
      {allowedActions.includes("approve") && openFlags.length > 0 && (
        <p className="flag-note">
          Approval needs every fraud flag confirmed or dismissed first ({openFlags.join(", ")}).
        </p>
      )}

      {mlroTurn && (
        <div className="row">
          <p className="muted">
            MLRO: decide whether to file a suspicious transaction report with STRO. You
            must not be the analyst who escalated.
          </p>
          <button
            type="button"
            className="btn danger"
            disabled={!ready}
            onClick={() => submit(() => onMlro("str_filed", reason.trim(), actor.trim()))}
          >
            File STR
          </button>
          <button
            type="button"
            className="btn ghost"
            disabled={!ready}
            onClick={() => submit(() => onMlro("no_str", reason.trim(), actor.trim()))}
          >
            No STR needed
          </button>
        </div>
      )}
    </section>
  );
}
