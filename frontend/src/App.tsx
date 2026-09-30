import { useCallback, useState } from "react";
import {
  createCase,
  createCaseFromPack,
  decideCase,
  decideFlag,
  getAudit,
} from "./api";
import { AgentPanel } from "./components/AgentPanel";
import { AuditTrail } from "./components/AuditTrail";
import { DecisionPanel } from "./components/DecisionPanel";
import { DocumentsPanel } from "./components/DocumentsPanel";
import { EvidenceList } from "./components/EvidenceList";
import { ReadinessPanel } from "./components/ReadinessPanel";
import { ScoreDial } from "./components/ScoreDial";
import { UploadPanel } from "./components/UploadPanel";
import type { AuditEvent, CaseView, FinalDecision, FlagDecision } from "./types";

export function App() {
  const [kase, setKase] = useState<CaseView | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  const record = async (write: () => Promise<unknown>) => {
    if (!kase) return;
    await guard(async () => {
      await write();
      setAudit(await getAudit(kase.case_id));
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
              <button type="button" className="btn ghost" onClick={() => setKase(null)}>
                New case
              </button>
            </div>

            <div className="grid">
              <ScoreDial risk={kase.risk} />
              {kase.readiness && <ReadinessPanel readiness={kase.readiness} />}
            </div>

            {kase.summary && (
              <section className="card" aria-labelledby="summary-title">
                <h2 id="summary-title">Summary</h2>
                <p className="summary">{kase.summary}</p>
              </section>
            )}

            <AgentPanel
              agents={kase.agent_reports}
              extractionMode={kase.extraction_mode}
              docCount={kase.docs.length}
            />

            <EvidenceList
              evidence={kase.evidence}
              audit={audit}
              onDecide={(id, decision: FlagDecision, reason) =>
                record(() => decideFlag(kase.case_id, id, decision, reason))
              }
            />
            <DocumentsPanel docs={kase.docs} />
            <DecisionPanel
              audit={audit}
              onDecide={(decision: FinalDecision, reason) =>
                record(() => decideCase(kase.case_id, decision, reason))
              }
            />
            <AuditTrail audit={audit} />
          </>
        )}
      </main>
    </>
  );
}
