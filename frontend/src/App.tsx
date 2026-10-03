import { useCallback, useState } from "react";
import {
  addDocuments,
  createCase,
  createCaseFromPack,
  decideAsMlro,
  decideCase,
  decideFlag,
  getAudit,
  getCase,
} from "./api";
import { AgentPanel } from "./components/AgentPanel";
import { AuditTrail } from "./components/AuditTrail";
import { ChainPanel } from "./components/ChainPanel";
import { DecisionPanel } from "./components/DecisionPanel";
import { DocumentViewer } from "./components/DocumentViewer";
import { DocumentsPanel } from "./components/DocumentsPanel";
import { EvidenceList } from "./components/EvidenceList";
import { InvestigationQueue } from "./components/InvestigationQueue";
import { ReadinessPanel } from "./components/ReadinessPanel";
import { RoutingBanner } from "./components/RoutingBanner";
import { ScoreDial } from "./components/ScoreDial";
import { SemanticReviewPanel } from "./components/SemanticReviewPanel";
import { TimelinePanel } from "./components/TimelinePanel";
import { UploadPanel } from "./components/UploadPanel";
import type { AuditEvent, CaseView, ViewTarget } from "./types";

export function App() {
  const [kase, setKase] = useState<CaseView | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewing, setViewing] = useState<ViewTarget | null>(null);

  const guard = useCallback(async (work: () => Promise<void>) => {
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    }
  }, []);

  const open = (create: () => Promise<CaseView>) => {
    setBusy(true);
    void guard(async () => {
      const created = await create();
      setAudit(await getAudit(created.case_id));
      setKase(created);
    }).finally(() => setBusy(false));
  };

  /** Write, then reload the case: its status and allowed actions are derived
   * on the server from the audit log, so they are never guessed here. */
  const record = async (write: () => Promise<unknown>) => {
    if (!kase) return;
    await guard(async () => {
      await write();
      const [fresh, events] = await Promise.all([
        getCase(kase.case_id),
        getAudit(kase.case_id),
      ]);
      setKase(fresh);
      setAudit(events);
    });
  };

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true" />
          <h1>GLDB Document Risk Review</h1>
        </div>
        <span className="badge">Prototype</span>
      </header>

      <p className="banner" role="note">
        Prototype using synthetic data only. Findings are aids for a human
        reviewer and do not prove or rule out fraud.
      </p>

      <main>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}

        {!kase ? (
          <UploadPanel
            busy={busy}
            onUpload={(files) => open(() => createCase(files))}
            onPack={(name) => open(() => createCaseFromPack(name))}
          />
        ) : (
          <>
            <div className="case-head">
              <div>
                <h2 className="case-title">{kase.case_id}</h2>
                <p className="muted">
                  {kase.docs.length} documents · extraction: {kase.extraction_mode}
                </p>
              </div>
              <button type="button" className="btn ghost" onClick={() => { setKase(null); setViewing(null); }}>
                New case
              </button>
            </div>

            {kase.routing && <RoutingBanner routing={kase.routing} />}

            <div className="grid">
              <ScoreDial risk={kase.risk} />
              {kase.readiness && <ReadinessPanel readiness={kase.readiness} />}
            </div>

            {kase.review && <SemanticReviewPanel review={kase.review} />}

            <AgentPanel
              agents={kase.agent_reports}
              extractionMode={kase.extraction_mode}
              docCount={kase.docs.length}
            />

            {kase.routing && (
              <InvestigationQueue
                tasks={kase.investigation}
                risk={kase.risk}
                path={kase.routing.path}
                openFlags={kase.open_flags}
                onView={setViewing}
                pageOf={(id) => kase.evidence.find((e) => e.evidence_id === id)?.page ?? 1}
              />
            )}

            <ChainPanel chains={kase.chains} onView={setViewing} />
            <TimelinePanel events={kase.timeline} onView={setViewing} />

            <EvidenceList
              evidence={kase.evidence}
              audit={audit}
              editable={kase.allowed_actions.length > 0}
              onDecide={(id, decision, reasonCode, note) =>
                record(() => decideFlag(kase.case_id, id, decision, reasonCode, note, "analyst"))
              }
              onView={setViewing}
            />
            <DocumentsPanel docs={kase.docs} />
            <DecisionPanel
              status={kase.status}
              allowedActions={kase.allowed_actions}
              openFlags={kase.open_flags}
              onAnalyst={(decision, reason, actor) =>
                record(() => decideCase(kase.case_id, decision, reason, actor))
              }
              onMlro={(decision, reason, actor) =>
                record(() => decideAsMlro(kase.case_id, decision, reason, actor))
              }
              onDocuments={(files) => record(() => addDocuments(kase.case_id, files))}
            />
            <AuditTrail audit={audit} />
            {viewing && (
              <DocumentViewer
                key={`${viewing.docId}-${viewing.page}-${viewing.evidenceId ?? ""}`}
                caseId={kase.case_id}
                docs={kase.docs}
                target={viewing}
                onClose={() => setViewing(null)}
              />
            )}
          </>
        )}
      </main>
    </>
  );
}
