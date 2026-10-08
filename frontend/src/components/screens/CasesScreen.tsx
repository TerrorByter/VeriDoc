import { useEffect, useState } from "react";
import { listCases } from "../../api";
import { when } from "../../catalogue";
import { href, navigate, type Route } from "../../route";
import type { CaseSummary } from "../../types";
import { BAND_TONE, Empty, ErrorNote, PathPill, Pill, StatusPill } from "../ui";

const target = (c: CaseSummary): Route =>
  c.status === "running" || c.status === "failed"
    ? { screen: "run", caseId: c.case_id }
    : { screen: "case", caseId: c.case_id, stage: "ocr" };

export function CasesScreen() {
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listCases().then(setCases).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Cases</h1>
          <p className="muted">Loan document sets submitted for review, newest first.</p>
        </div>
        <a className="btn primary" href={href({ screen: "new" })}>New case</a>
      </div>

      {error && <ErrorNote>Could not load cases: {error}. Check that the API server is running.</ErrorNote>}

      {cases === null && !error && (
        <div className="table-wrap" aria-busy="true">
          {[0, 1, 2].map((i) => <div key={i} className="skeleton row-skeleton" />)}
        </div>
      )}

      {cases?.length === 0 && (
        <Empty title="No cases yet">
          Start one from a demo pack or upload a document set. <a href={href({ screen: "new" })}>New case</a>
        </Empty>
      )}

      {cases && cases.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">Case</th>
                <th scope="col">Applicant</th>
                <th scope="col" className="num">Documents</th>
                <th scope="col">Route</th>
                <th scope="col">Fraud risk</th>
                <th scope="col">Status</th>
                <th scope="col">Opened</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => (
                <tr key={c.case_id} className="clickable" onClick={() => navigate(target(c))}>
                  <td>
                    <a className="mono" href={href(target(c))} onClick={(e) => e.stopPropagation()}>
                      {c.case_id}
                    </a>
                  </td>
                  <td>{c.company_name ?? <span className="muted">—</span>}</td>
                  <td className="num">{c.doc_count || "—"}</td>
                  <td>{c.path ? <PathPill path={c.path} /> : <span className="muted">—</span>}</td>
                  <td>
                    {c.band ? (
                      <Pill tone={BAND_TONE[c.band]}>{c.band} · {c.score}</Pill>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td><StatusPill status={c.status} /></td>
                  <td className="muted nowrap">{when(c.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
