import { useEffect, useState } from "react";
import { getEvidenceLocation, pageUrl } from "../../api";
import { DOC_TYPE_LABEL } from "../../catalogue";
import type { DocMeta, EvidenceLocation, ViewTarget } from "../../types";
import { Icon } from "../ui";

/**
 * A case document page beside the stage detail, with the selected finding's
 * quote highlighted. Mounted once per target (keyed by the caller), so its
 * state starts fresh each time.
 */
export function DocumentPane({
  caseId,
  docs,
  target,
  onPick,
}: {
  caseId: string;
  docs: DocMeta[];
  target: ViewTarget;
  onPick: (target: ViewTarget) => void;
}) {
  const [page, setPage] = useState(target.page);
  const [loaded, setLoaded] = useState(false);
  const [location, setLocation] = useState<EvidenceLocation | null>(null);
  const [locating, setLocating] = useState(Boolean(target.evidenceId));
  const doc = docs.find((d) => d.doc_id === target.docId);
  const pageCount = doc?.page_count ?? 1;

  useEffect(() => {
    if (!target.evidenceId) return;
    getEvidenceLocation(caseId, target.evidenceId)
      .then(setLocation)
      .catch(() => setLocation(null))
      .finally(() => setLocating(false));
  }, [caseId, target.evidenceId]);

  const goTo = (next: number) => {
    setLoaded(false);
    setPage(next);
  };
  const boxes = location && location.page === page ? location.boxes : [];
  const src = pageUrl(caseId, target.docId, page);

  let status: string | null = null;
  if (target.evidenceId) {
    if (locating) status = `Finding ${target.evidenceId} on the page…`;
    else if (boxes.length) status = `${target.evidenceId} highlighted`;
    else if (location?.page === page)
      status = `${target.evidenceId} is on this page, but its text cannot be placed (a scan without positions, or no quote).`;
  }

  return (
    <aside className="doc-pane" aria-label="Document viewer">
      <div className="doc-bar">
        <label className="sr-only" htmlFor="doc-pick">Document</label>
        <select
          id="doc-pick"
          value={target.docId}
          onChange={(e) => onPick({ docId: e.target.value, page: 1 })}
        >
          {docs.map((d) => (
            <option key={d.doc_id} value={d.doc_id}>{d.filename}</option>
          ))}
        </select>
        <div className="doc-nav">
          <button type="button" className="icon-btn" aria-label="Previous page" disabled={page <= 1} onClick={() => goTo(page - 1)}>
            <Icon name="left" size={14} />
          </button>
          <span className="mono small">{page} / {pageCount}</span>
          <button type="button" className="icon-btn" aria-label="Next page" disabled={page >= pageCount} onClick={() => goTo(page + 1)}>
            <Icon name="right" size={14} />
          </button>
          <a className="icon-btn" href={src} target="_blank" rel="noreferrer" aria-label="Open page full size in a new tab">
            <Icon name="external" size={14} />
          </a>
        </div>
      </div>
      <p className={`doc-status${boxes.length ? " found" : ""}`} role="status">
        {status ?? (doc ? DOC_TYPE_LABEL[doc.doc_type] ?? doc.doc_type : "")}
      </p>
      <div className="doc-scroll">
        <div className={`sheet${loaded ? "" : " loading"}`}>
          <img src={src} alt={`${doc?.filename ?? target.docId}, page ${page}`} onLoad={() => setLoaded(true)} />
          {loaded &&
            boxes.map((b, i) => (
              <span
                key={i}
                className="highlight"
                aria-hidden="true"
                style={{
                  left: `${b.x0 * 100}%`,
                  top: `${b.top * 100}%`,
                  width: `${(b.x1 - b.x0) * 100}%`,
                  height: `${(b.bottom - b.top) * 100}%`,
                }}
              />
            ))}
        </div>
      </div>
    </aside>
  );
}
