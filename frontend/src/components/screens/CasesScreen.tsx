import { useEffect, useState } from "react";
import { listCases } from "../../api";
import { PATH_LABEL, STATUS_LABEL, when } from "../../catalogue";
import { href, navigate, type Route } from "../../route";
import type { CaseCategory, CaseSummary, ReviewPath } from "../../types";
import { BAND_TONE, Empty, ErrorNote, Icon, PathPill, tabKeys } from "../ui";

/** Cases listed before "Show all" */
const FIRST = 10;

type Filter = "all" | ReviewPath;
const FILTERS: readonly Filter[] = ["all", "enhanced_review", "standard_review", "fast_track"];

const target = (c: CaseSummary): Route =>
  c.status === "running" || c.status === "failed"
    ? { screen: "run", caseId: c.case_id }
    : { screen: "case", caseId: c.case_id, stage: "ocr" };

/** Categories shown before "+N more" */
const CATEGORIES_SHOWN = 2;

/** What kind of problem the case shows, as code set it; AI-team categories are marked. */
function Categories({ c }: { c: CaseSummary }) {
  if (c.status === "running" || c.status === "failed") return <span className="muted">—</span>;
  const all = c.categories ?? [];
  if (all.length === 0) return <span className="muted">No issues found</span>;
  const describe = (k: CaseCategory) =>
    `${k.label}${k.source === "ai_team" ? " (AI team, verified by code)" : ""}: ${k.ids.join(", ")}`;
  const rest = all.slice(CATEGORIES_SHOWN);
  return (
    <ul className="categories" aria-label="Categories">
      {all.slice(0, CATEGORIES_SHOWN).map((k) => (
        <li key={`${k.source}-${k.label}`} className="category" title={describe(k)}>
          {k.label}
          {k.source === "ai_team" && <span className="category-ai" aria-label="from the AI team">AI</span>}
        </li>
      ))}
      {rest.length > 0 && (
        <li className="category more muted" title={rest.map(describe).join("\n")}>+{rest.length} more</li>
      )}
    </ul>
  );
}

const matches = (c: CaseSummary, query: string) =>
  !query || `${c.company_name ?? ""} ${c.case_id}`.toLowerCase().includes(query.toLowerCase());

export function CasesScreen() {
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [all, setAll] = useState(false);

  useEffect(() => {
    listCases().then(setCases).catch((e: Error) => setError(e.message));
  }, []);

  const searched = (cases ?? []).filter((c) => matches(c, query.trim()));
  const shown = searched.filter((c) => filter === "all" || c.path === filter);
  const listed = all ? shown : shown.slice(0, FIRST);
  const count = (f: Filter) => (f === "all" ? searched.length : searched.filter((c) => c.path === f).length);

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Past cases</h1>
          <p className="muted">Every application run so far, newest first.</p>
        </div>
      </div>

      {error && <ErrorNote>Could not load cases: {error}. Check that the API server is running.</ErrorNote>}

      {cases === null && !error && (
        <div className="table-wrap" aria-busy="true">
          {[0, 1, 2].map((i) => <div key={i} className="skeleton row-skeleton" />)}
        </div>
      )}

      {cases?.length === 0 && (
        <Empty title="No cases yet">
          Run a demo pack or upload an application's documents. <a href={href({ screen: "new" })}>New case</a>
        </Empty>
      )}

      {cases && cases.length > 0 && (
        <section className="cases" aria-label="Past cases">
          <div className="cases-tools">
            <label className="search">
              <Icon name="search" size={14} />
              <span className="sr-only">Search by applicant or case ID</span>
              <input type="search" placeholder="Search applicant or case ID" value={query}
                onChange={(e) => setQuery(e.target.value)} />
            </label>
            <div className="segmented" role="tablist" aria-label="Route" onKeyDown={tabKeys(FILTERS, filter, setFilter)}>
              {FILTERS.map((f) => (
                <button
                  key={f}
                  id={`route-tab-${f}`}
                  type="button"
                  role="tab"
                  aria-selected={filter === f}
                  aria-controls="cases-panel"
                  tabIndex={filter === f ? 0 : -1}
                  onClick={() => setFilter(f)}
                >
                  {f === "all" ? "All" : PATH_LABEL[f]} <span className="count">{count(f)}</span>
                </button>
              ))}
            </div>
          </div>

          <div id="cases-panel" role="tabpanel" aria-labelledby={`route-tab-${filter}`}>
            {shown.length === 0 ? (
              <p className="muted cases-none">No cases match.</p>
            ) : (
              <div className="table-wrap">
                <table className="data cases-table">
                  <thead>
                    <tr>
                      <th scope="col">Applicant</th>
                      <th scope="col">Route</th>
                      <th scope="col">Risk</th>
                      <th scope="col">Category</th>
                      <th scope="col">Status</th>
                      <th scope="col">Opened</th>
                    </tr>
                  </thead>
                  <tbody>
                    {listed.map((c) => (
                      <tr key={c.case_id} className="clickable" onClick={() => navigate(target(c))}>
                        <td>
                          <a className="case-name" href={href(target(c))} onClick={(e) => e.stopPropagation()}>
                            {c.company_name ?? "Unnamed applicant"}
                          </a>
                          <span className="case-meta muted">
                            <span className="mono">{c.case_id}</span>
                            {c.doc_count > 0 && <> · {c.doc_count} documents</>}
                          </span>
                        </td>
                        <td>{c.path ? <PathPill path={c.path} /> : <span className="muted">—</span>}</td>
                        <td>
                          {c.band ? (
                            <span className={`risk ${BAND_TONE[c.band]}`}>{c.band} <span className="muted">{c.score}</span></span>
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                        <td><Categories c={c} /></td>
                        <td className={c.status === "failed" ? "status failed" : "status muted"}>{STATUS_LABEL[c.status]}</td>
                        <td className="muted nowrap">{when(c.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {shown.length > FIRST && (
              <button type="button" className="btn ghost small cases-more" onClick={() => setAll(!all)}>
                {all ? "Show fewer" : `Show all ${shown.length}`}
              </button>
            )}
          </div>
        </section>
      )}
    </main>
  );
}
