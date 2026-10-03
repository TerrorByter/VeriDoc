import { useEffect, useRef, useState } from "react";
import { getEvidenceLocation, pageUrl } from "../api";
import type { DocMeta, EvidenceLocation, ViewTarget } from "../types";

/**
 * A case document page with the finding's quote highlighted. Mounted once
 * per target (keyed by the caller), so its state starts fresh each time.
 */
export function DocumentViewer({
  caseId,
  docs,
  target,
  onClose,
}: {
  caseId: string;
  docs: DocMeta[];
  target: ViewTarget;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [page, setPage] = useState(target.page);
  const [loaded, setLoaded] = useState(false);
  const [location, setLocation] = useState<EvidenceLocation | null>(null);
  const [locating, setLocating] = useState(Boolean(target.evidenceId));
  const doc = docs.find((d) => d.doc_id === target.docId);
  const pageCount = doc?.page_count ?? 1;

  useEffect(() => {
    dialog.current?.showModal();
  }, []);

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

  let status: string | null = null;
  if (target.evidenceId) {
    if (locating) status = `Locating ${target.evidenceId}…`;
    else if (location?.highlighted) status = `${target.evidenceId}: highlighted "${location.highlighted}"`;
    else
      status = `${target.evidenceId} is on this page, but its text cannot be placed: the page is a scan with no text layer, or the finding has no quote.`;
  }

  return (
    <dialog
      ref={dialog}
      className="viewer"
      aria-labelledby="viewer-title"
      onClose={onClose}
      onClick={(e) => e.target === dialog.current && dialog.current?.close()}
    >
      <header className="viewer-head">
        <div>
          <h2 id="viewer-title">{doc?.filename ?? target.docId}</h2>
          <p className="muted">
            Page {page} of {pageCount}
            {doc && ` · ${doc.doc_type.replace(/_/g, " ")}`}
          </p>
        </div>
        <div className="row">
          <button type="button" className="btn chip" disabled={page <= 1} onClick={() => goTo(page - 1)}>
            ← Previous
          </button>
          <button type="button" className="btn chip" disabled={page >= pageCount} onClick={() => goTo(page + 1)}>
            Next →
          </button>
          <button type="button" className="btn ghost" onClick={() => dialog.current?.close()}>
            Close
          </button>
        </div>
      </header>
      {status && (
        <p className={`viewer-status${location?.boxes.length ? " found" : ""}`} role="status">
          {status}
        </p>
      )}
      <div className={`page-frame${loaded ? "" : " loading"}`}>
        <img
          src={pageUrl(caseId, target.docId, page)}
          alt={`${doc?.filename ?? target.docId}, page ${page}`}
          onLoad={() => setLoaded(true)}
        />
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
    </dialog>
  );
}
