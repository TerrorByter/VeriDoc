import { useEffect, useRef, useState } from "react";
import { addDocuments, decideAsMlro, decideCase, getRequestDraft } from "../../api";
import { STATUS_LABEL } from "../../catalogue";
import type { AnalystDecision, CaseView } from "../../types";
import { ErrorNote, Icon } from "../ui";

const ANALYST: { key: AnalystDecision; label: string; tone: string }[] = [
  { key: "approve", label: "Approve", tone: "primary" },
  { key: "request_info", label: "Request information", tone: "" },
  { key: "escalate", label: "Escalate to MLRO", tone: "" },
  { key: "decline", label: "Decline", tone: "danger" },
];

/** Every decision is a person's: the system never approves, declines or reports. */
export function DecisionDrawer({
  kase,
  onClose,
  onRecorded,
  onRerun,
}: {
  kase: CaseView;
  onClose: () => void;
  onRecorded: () => Promise<void>;
  onRerun: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const mlroTurn = kase.allowed_actions.includes("mlro_decision");
  const [reason, setReason] = useState("");
  const [actor, setActor] = useState(mlroTurn ? "mlro" : "analyst");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const ready = reason.trim() !== "" && actor.trim() !== "" && !saving;
  const actions = ANALYST.filter((a) => kase.allowed_actions.includes(a.key));
  const openFlags = kase.open_flags;

  useEffect(() => {
    dialog.current?.showModal();
  }, []);

  const submit = (work: () => Promise<unknown>, after: () => void | Promise<void>) => {
    setSaving(true);
    setError(null);
    work()
      .then(after)
      .then(() => dialog.current?.close())
      .catch((e: Error) => setError(e.message))
      .finally(() => setSaving(false));
  };

  return (
    <dialog
      ref={dialog}
      className="drawer"
      aria-labelledby="decision-title"
      onClose={onClose}
      onClick={(e) => e.target === dialog.current && dialog.current?.close()}
    >
      <header className="drawer-head">
        <div>
          <h2 id="decision-title">{mlroTurn ? "MLRO decision" : "Record a decision"}</h2>
          <p className="muted">{STATUS_LABEL[kase.status]}</p>
        </div>
        <button type="button" className="icon-btn" aria-label="Close" onClick={() => dialog.current?.close()}>
          <Icon name="close" size={14} />
        </button>
      </header>

      <div className="drawer-body">
        <p className="muted">
          The system never approves, declines or reports a case. Your decision and reason are written
          to the audit log under your name.
        </p>
        {error && <ErrorNote>{error}</ErrorNote>}

        {kase.allowed_actions.includes("documents_added") && (
          <fieldset className="field-group">
            <legend>Documents from the applicant</legend>
            <input
              id="rfi-files"
              type="file"
              multiple
              accept="application/pdf,.xlsx"
              aria-label="Requested documents"
              onChange={(e) => setFiles([...(e.target.files ?? [])])}
            />
            <button
              type="button"
              className="btn primary"
              disabled={files.length === 0 || saving}
              onClick={() => submit(() => addDocuments(kase.case_id, files), onRerun)}
            >
              Add documents and re-run
            </button>
          </fieldset>
        )}

        {(actions.length > 0 || mlroTurn) && (
          <>
            <label className="field">
              <span>{mlroTurn ? "MLRO name" : "Analyst name"}</span>
              <input id="decision-actor" type="text" value={actor} onChange={(e) => setActor(e.target.value)} />
            </label>
            <label className="field">
              <span>Reason</span>
              <textarea
                id="decision-reason"
                rows={4}
                value={reason}
                placeholder="Why you are making this decision"
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            {actions.some((a) => a.key === "request_info") && (
              <button
                type="button"
                className="link"
                disabled={saving}
                onClick={() => getRequestDraft(kase.case_id)
                  .then((d) => setReason(d.text || reason))
                  .catch((e: Error) => setError(e.message))}
              >
                Use the drafted request for information
              </button>
            )}
          </>
        )}

        {actions.length > 0 && (
          <div className="decision-grid">
            {actions.map(({ key, label, tone }) => {
              const blocked = key === "approve" && openFlags.length > 0;
              return (
                <button
                  key={key}
                  type="button"
                  className={`btn ${tone}`}
                  disabled={!ready || blocked}
                  onClick={() => submit(() => decideCase(kase.case_id, key, reason.trim(), actor.trim()), onRecorded)}
                >
                  {label}
                </button>
              );
            })}
          </div>
        )}
        {kase.routing?.edd_required && kase.status === "assessed" && (
          <p className="hint">
            Enhanced review: the MLRO must review this case before it can be approved. Escalate it to the MLRO.
          </p>
        )}
        {kase.allowed_actions.includes("approve") && openFlags.length > 0 && (
          <p className="hint">
            Approval needs every fraud flag confirmed or dismissed first. Open: {openFlags.join(", ")}.
          </p>
        )}

        {mlroTurn && (
          <>
            <p className="hint">
              Decide whether to file a suspicious transaction report with STRO. You must not be the
              analyst who escalated the case.
              {kase.routing?.edd_required && " This case is on enhanced review: your review is the senior sign-off that lets the analyst approve it."}
            </p>
            <div className="decision-grid">
              <button
                type="button"
                className="btn danger"
                disabled={!ready}
                onClick={() => submit(() => decideAsMlro(kase.case_id, "str_filed", reason.trim(), actor.trim()), onRecorded)}
              >
                File STR
              </button>
              <button
                type="button"
                className="btn"
                disabled={!ready}
                onClick={() => submit(() => decideAsMlro(kase.case_id, "no_str", reason.trim(), actor.trim()), onRecorded)}
              >
                No STR needed
              </button>
            </div>
          </>
        )}
      </div>
    </dialog>
  );
}
