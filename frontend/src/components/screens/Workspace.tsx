import { useCallback, useEffect, useState } from "react";
import { decideFlag, getAudit, getCase } from "../../api";
import { STAGE_BY_ID, STAGES } from "../../catalogue";
import { href, navigate } from "../../route";
import type { AuditEvent, CaseView, FlagDecision, ReasonCode, RailStageId, ViewTarget } from "../../types";
import { ErrorNote } from "../ui";
import { CaseHeader } from "../workspace/CaseHeader";
import { DecisionDrawer } from "../workspace/DecisionDrawer";
import { DocumentPane } from "../workspace/DocumentPane";
import { StageDetail } from "../workspace/StageDetail";
import { StageRail } from "../workspace/StageRail";

/** Arrow keys step through stages, unless the person is typing. */
function useStageKeys(caseId: string, stage: RailStageId) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el.closest("input, textarea, select, dialog[open]") || e.metaKey || e.ctrlKey || e.altKey) return;
      const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      const next = STAGES[STAGE_BY_ID[stage].n - 1 + step];
      if (step && next) {
        e.preventDefault();
        navigate({ screen: "case", caseId, stage: next.id });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [caseId, stage]);
}

export function Workspace({ caseId, stage }: { caseId: string; stage: RailStageId }) {
  const [kase, setKase] = useState<CaseView | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [viewing, setViewing] = useState<ViewTarget | null>(null);
  const [deciding, setDeciding] = useState(false);
  useStageKeys(caseId, stage);

  /** Status and allowed actions are derived on the server from the audit
   * log, so after every write the case is reloaded, never patched here. */
  const reload = useCallback(
    () =>
      Promise.all([getCase(caseId), getAudit(caseId)])
        .then(([fresh, events]) => {
          setKase(fresh);
          setAudit(events);
          setError(null);
        })
        .catch((e: Error) => setError(e.message)),
    [caseId],
  );

  useEffect(() => {
    void reload();
  }, [reload]);

  /** Open a page in the document pane; where the pane sits below the stage, bring it into view and focus it. */
  const view = (target: ViewTarget) => {
    setViewing(target);
    if (window.matchMedia("(max-width: 1180px)").matches) {
      requestAnimationFrame(() => {
        const pane = document.querySelector<HTMLElement>(".doc-pane");
        pane?.scrollIntoView({ block: "start" });
        pane?.focus({ preventScroll: true });
      });
    }
  };

  const write = (work: () => Promise<unknown>) =>
    work().then(reload, (e: Error) => setError(e.message));

  const onDecideFlag = (id: string, decision: FlagDecision, code: ReasonCode | null, note: string) =>
    write(() => decideFlag(caseId, id, decision, code, note, "analyst"));

  if (!kase) {
    return (
      <main className="page">
        {error ? (
          <ErrorNote>
            Could not open {caseId}: {error}. <a href={href({ screen: "cases" })}>Back to past cases</a>
          </ErrorNote>
        ) : (
          <div className="skeleton block-skeleton" aria-busy="true" aria-label="Loading case" />
        )}
      </main>
    );
  }

  const target = viewing ?? (kase.docs[0] ? { docId: kase.docs[0].doc_id, page: 1 } : null);

  return (
    <main className="workspace-screen">
      <CaseHeader kase={kase} onDecide={() => setDeciding(true)} />
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="workspace">
        <StageRail kase={kase} audit={audit} current={stage} />
        <StageDetail
          key={stage}
          stage={stage}
          kase={kase}
          audit={audit}
          onView={view}
          onDecideFlag={onDecideFlag}
        />
        {target && (
          <DocumentPane
            key={`${target.docId}-${target.page}-${target.evidenceId ?? ""}`}
            caseId={kase.case_id}
            docs={kase.docs}
            target={target}
            onPick={setViewing}
          />
        )}
      </div>
      {deciding && (
        <DecisionDrawer
          kase={kase}
          onClose={() => setDeciding(false)}
          onRecorded={reload}
          onRerun={() => navigate({ screen: "run", caseId })}
        />
      )}
    </main>
  );
}
