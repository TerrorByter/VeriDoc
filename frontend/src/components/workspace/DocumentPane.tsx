import { useEffect, useState } from "react";
import { getEvidenceLocation, getPageFindings, pageUrl } from "../../api";
import { DOC_TYPE_LABEL } from "../../catalogue";
import type { Box, DocMeta, EvidenceLocation, PageFinding, ViewTarget } from "../../types";
import { Icon } from "../ui";

const OVERLAY_KEY = "veridoc.forensic-overlay";

/** Whether the viewer last showed the forensic overlay: a per-viewer convenience, off if storage is unavailable */
function overlayRemembered(): boolean {
  try {
    return localStorage.getItem(OVERLAY_KEY) === "on";
  } catch {
    return false;
  }
}

function rememberOverlay(on: boolean) {
  try {
    localStorage.setItem(OVERLAY_KEY, on ? "on" : "off");
  } catch {
    // The overlay still works; it is just not remembered
  }
}

const boxStyle = (b: Box) => ({
  left: `${b.x0 * 100}%`,
  top: `${b.top * 100}%`,
  width: `${(b.x1 - b.x0) * 100}%`,
  height: `${(b.bottom - b.top) * 100}%`,
});

/**
 * A case document page beside the stage detail, with the selected finding's
 * quote highlighted. The forensic overlay boxes every finding on the page at
 * once, coloured by severity; hovering one says what it is, clicking it
 * selects it. Mounted once per target (keyed by the caller), so its state
 * starts fresh each time; the overlay choice is remembered across targets.
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
  const [failed, setFailed] = useState(false);
  const [location, setLocation] = useState<EvidenceLocation | null>(null);
  const [locating, setLocating] = useState(Boolean(target.evidenceId));
  const [overlay, setOverlay] = useState(overlayRemembered);
  const [findings, setFindings] = useState<PageFinding[] | null>(null);
  const doc = docs.find((d) => d.doc_id === target.docId);
  const pageCount = doc?.page_count ?? 1;

  useEffect(() => {
    if (!target.evidenceId) return;
    getEvidenceLocation(caseId, target.evidenceId, target.row)
      .then(setLocation)
      .catch(() => setLocation(null))
      .finally(() => setLocating(false));
  }, [caseId, target.evidenceId, target.row]);

  useEffect(() => {
    if (!overlay) return;
    let current = true;
    setFindings(null);
    getPageFindings(caseId, target.docId, page)
      .then((found) => current && setFindings(found))
      .catch(() => current && setFindings([]));
    return () => {
      current = false;
    };
  }, [caseId, target.docId, page, overlay]);

  const goTo = (next: number) => {
    setLoaded(false);
    setFailed(false);
    setPage(next);
  };
  const showOverlay = (on: boolean) => {
    setOverlay(on);
    rememberOverlay(on);
  };
  const boxes = location && location.page === page ? location.boxes : [];
  const src = pageUrl(caseId, target.docId, page);
  const placed = overlay && findings ? findings.filter((f) => f.boxes.length) : [];
  const unplaced = overlay && findings ? findings.filter((f) => !f.boxes.length) : [];

  let status: string | null = null;
  if (target.evidenceId) {
    if (locating) status = `Finding ${target.evidenceId} on the page…`;
    else if (boxes.length) status = `${target.evidenceId} highlighted`;
    else if (location?.page === page)
      status = `${target.evidenceId} is on this page, but its text cannot be placed (a scan without positions, or no quote).`;
  }
  if (overlay && !status) {
    if (findings === null) status = "Reading this page's findings…";
    else if (!findings.length) status = "No findings on this page.";
    else status = `${findings.length} finding${findings.length === 1 ? "" : "s"} on this page`;
  }

  return (
    <aside className="doc-pane" aria-label="Document viewer" tabIndex={-1}>
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
      <div className="doc-mode">
        <div className="segmented" role="group" aria-label="Page view">
          <button type="button" aria-pressed={!overlay} onClick={() => showOverlay(false)}>
            Document
          </button>
          <button type="button" aria-pressed={overlay} onClick={() => showOverlay(true)}>
            Forensic overlay
          </button>
        </div>
        {overlay && (
          <ul className="overlay-key" aria-label="Severity colours">
            <li className="sev-critical">Critical</li>
            <li className="sev-high">High</li>
            <li className="sev-medium">Medium</li>
            <li className="sev-low">Low</li>
          </ul>
        )}
      </div>
      <p className={`doc-status${boxes.length || placed.length ? " found" : ""}`} role="status">
        {status ?? (doc ? DOC_TYPE_LABEL[doc.doc_type] ?? doc.doc_type : "")}
      </p>
      {unplaced.length > 0 && (
        <ul className="overlay-unplaced" aria-label="Findings on this page that are not drawn">
          {unplaced.map((f) => (
            <li key={f.evidence_id}>
              <button type="button" className={`sev-${f.severity}`} onClick={() => onPick({ docId: target.docId, page, evidenceId: f.evidence_id })}>
                <span className="mono">{f.evidence_id}</span> {f.title}
              </button>
              <span className="small"> is about the whole file, or its text has no place on the page</span>
            </li>
          ))}
        </ul>
      )}
      <div className="doc-scroll">
        {failed ? (
          <p className="doc-error" role="alert">
            This page could not be loaded. Try another page, or open it full size.
          </p>
        ) : (
          <div className={`sheet${loaded ? "" : " loading"}${overlay ? " overlaid" : ""}`}>
            <img
              src={src}
              alt={`${doc?.filename ?? target.docId}, page ${page}`}
              onLoad={() => setLoaded(true)}
              onError={() => setFailed(true)}
            />
            {loaded &&
              placed.map((f) =>
                f.boxes.map((b, i) => (
                  <button
                    key={`${f.evidence_id}-${i}`}
                    type="button"
                    className={`overlay-box sev-${f.severity}${b.x0 > 0.5 ? " tip-left" : ""}${b.top > 0.75 ? " tip-up" : ""}`}
                    style={boxStyle(b)}
                    aria-label={`${f.evidence_id}, ${f.severity}: ${f.title}. Show this finding.`}
                    aria-describedby={`tip-${f.evidence_id}-${i}`}
                    onClick={() => onPick({ docId: target.docId, page, evidenceId: f.evidence_id })}
                  >
                    <span className="overlay-tip" role="tooltip" id={`tip-${f.evidence_id}-${i}`}>
                      <span className="overlay-tip-head">
                        <span className="mono">{f.evidence_id}</span> {f.title} <em>{f.severity}</em>
                      </span>
                      {f.observed && <span className="overlay-tip-observed">{f.observed}</span>}
                      <span>{f.explanation}</span>
                    </span>
                  </button>
                )),
              )}
            {loaded &&
              boxes.map((b, i) => (
                <span key={i} className="highlight" aria-hidden="true" style={boxStyle(b)} />
              ))}
          </div>
        )}
      </div>
    </aside>
  );
}
