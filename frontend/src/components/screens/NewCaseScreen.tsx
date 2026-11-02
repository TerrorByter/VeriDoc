import { useEffect, useState } from "react";
import { createCase, createCaseFromPack, identifyDocument, listPacks } from "../../api";
import { DOC_TYPE_LABEL } from "../../catalogue";
import { ACCEPTED, REQUIRED_DOCUMENTS } from "../../requiredDocuments";
import { navigate } from "../../route";
import type { Identified, PackInfo, RunStarted } from "../../types";
import { DropArea } from "../DropArea";
import { ErrorNote, Icon, PathPill, tabKeys } from "../ui";

type Source = "demo" | "upload";
const SOURCES: readonly Source[] = ["demo", "upload"];
const SOURCE_LABEL: Record<Source, string> = { demo: "Demo pack", upload: "Upload documents" };

/** "invoice_mismatch" -> "Invoice mismatch" */
const packTitle = (name: string) => name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

const typeLabel = (type: string) => (type === "application" ? "Application data" : DOC_TYPE_LABEL[type] ?? type);

/** The order files are listed in: unusable ones first (they block the run), then the required documents,
 * the application data, the trade paperwork and anything unrecognised; files still being checked last. */
const ORDER = [...REQUIRED_DOCUMENTS.map((d) => d.type), "application", "invoice", "purchase_order", "bill_of_lading", "delivery_order"];
const rank = ({ found }: Held) =>
  found === null ? ORDER.length + 2 : found.error ? -1 : ORDER.includes(found.doc_type) ? ORDER.indexOf(found.doc_type) : ORDER.length + 1;

/** A file the analyst added, and what the server read it as (null while it is being checked) */
interface Held {
  file: File;
  found: Identified | null;
}

export function NewCaseScreen() {
  const [packs, setPacks] = useState<PackInfo[]>([]);
  const [starting, setStarting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<Source>("demo");

  useEffect(() => {
    listPacks().then(setPacks).catch(() => setPacks([]));
  }, []);

  const start = (label: string, begin: () => Promise<RunStarted>) => {
    setStarting(label);
    setError(null);
    begin()
      .then(({ case_id }) => navigate({ screen: "run", caseId: case_id }))
      .catch((e: Error) => {
        setError(e.message);
        setStarting(null);
      });
  };

  return (
    <main className="page narrow">
      <div className="page-head">
        <div>
          <h1>New case</h1>
          <p className="muted">Run a loan application's documents through the checks, and follow each stage as it runs.</p>
        </div>
        <div className="segmented" role="tablist" aria-label="Start from" onKeyDown={tabKeys(SOURCES, source, setSource)}>
          {SOURCES.map((key) => (
            <button
              key={key}
              id={`source-tab-${key}`}
              type="button"
              role="tab"
              aria-selected={source === key}
              aria-controls={`source-panel-${key}`}
              tabIndex={source === key ? 0 : -1}
              onClick={() => setSource(key)}
            >
              {SOURCE_LABEL[key]}
            </button>
          ))}
        </div>
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}

      {source === "demo" && (
        <section className="panel" id="source-panel-demo" role="tabpanel" aria-labelledby="source-tab-demo">
          <p className="muted">
            Synthetic applicants in the format of the team's sample. Each shows one kind of risk; pick one to run it.
          </p>
          <ul className="pack-grid">
            {packs.map(({ name, note, expected_path }) => (
              <li key={name}>
                <button
                  type="button"
                  className="pack"
                  disabled={starting !== null}
                  onClick={() => start(name, () => createCaseFromPack(name))}
                >
                  <span className="pack-head">
                    <span className="pack-name">{packTitle(name)}</span>
                    {starting === name ? <span className="spinner" aria-label="Starting" /> : <Icon name="right" size={14} />}
                  </span>
                  <span className="pack-note muted">{note ?? "Synthetic document set."}</span>
                  {expected_path && (
                    <span className="pack-expect">
                      <span className="muted small">Expected route</span> <PathPill path={expected_path} />
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {source === "upload" && (
        <Upload starting={starting === "upload"} onError={setError}
          onRun={(files) => start("upload", () => createCase(files))} />
      )}
    </main>
  );
}

/** One drop area for every file of the application. Each file is identified by the server as it is added,
 * and the run waits until every required document is there and every file is usable. */
function Upload({ starting, onError, onRun }: {
  starting: boolean;
  onError: (message: string | null) => void;
  onRun: (files: File[]) => void;
}) {
  const [held, setHeld] = useState<Held[]>([]);

  const identified = (file: File, found: Identified) =>
    setHeld((now) => now.map((h) => (h.file === file ? { ...h, found } : h)));

  /** A file name is used once in a case: the server keeps files by name. */
  const add = (incoming: File[]) => {
    const taken = new Set(held.map((h) => h.file.name));
    const repeated = incoming.filter((f) => taken.has(f.name));
    const fresh = incoming.filter((f) => !taken.has(f.name));
    onError(repeated.length ? `Already added: ${repeated.map((f) => f.name).join(", ")}.` : null);
    setHeld((now) => [...now, ...fresh.map((file) => ({ file, found: null }))]);
    for (const file of fresh) {
      identifyDocument(file)
        .then((found) => identified(file, found))
        .catch((e: Error) => identified(file, { filename: file.name, doc_type: "unknown", error: `Could not be checked: ${e.message}` }));
    }
  };

  const remove = (file: File) => setHeld((now) => now.filter((h) => h.file !== file));

  const usable = held.filter((h) => h.found && !h.found.error);
  const ofType = (type: string) => usable.filter((h) => h.found?.doc_type === type).length;
  const missing = REQUIRED_DOCUMENTS.filter((d) => ofType(d.type) === 0);
  const checking = held.some((h) => h.found === null);
  const unusable = held.filter((h) => h.found?.error);
  const ready = held.length > 0 && missing.length === 0 && !checking && unusable.length === 0;
  const waiting = checking
    ? "Checking files…"
    : unusable.length
      ? `Remove ${unusable.length === 1 ? "the file" : `${unusable.length} files`} that cannot be used`
      : missing.length
        ? `${missing.length} required document${missing.length === 1 ? "" : "s"} missing`
        : null;

  return (
    <section className="panel upload" id="source-panel-upload" role="tabpanel" aria-labelledby="source-tab-upload">
      <div className="upload-files">
        <DropArea accept={ACCEPTED} onFiles={add} />
        {held.length > 0 && (
          <ul className="file-list" aria-label="Files added">
            {[...held].sort((a, b) => rank(a) - rank(b) || a.file.name.localeCompare(b.file.name)).map(({ file, found }) => (
              <li key={file.name} className={found?.error ? "bad" : undefined}>
                <Icon name="doc" size={14} />
                <span className="file-name">
                  <span className="mono">{file.name}</span>
                  {found?.error && <span className="file-error">{found.error}</span>}
                  {found && !found.error && found.doc_type === "unknown" && (
                    <span className="muted small">Not recognised: read with OCR when the case runs</span>
                  )}
                </span>
                <span className="file-type">
                  {found === null ? <span className="spinner" aria-label="Checking" /> : found.error ? "Cannot use" : typeLabel(found.doc_type)}
                </span>
                <button type="button" className="btn ghost small" aria-label={`Remove ${file.name}`} onClick={() => remove(file)}>
                  <Icon name="close" size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <aside className="upload-check" aria-label="Required documents">
        <h2>Required documents</h2>
        <ul className="required-list" aria-live="polite">
          {REQUIRED_DOCUMENTS.map((d) => {
            const n = ofType(d.type);
            return (
              <li key={d.type} className={n ? "done" : undefined}>
                <span className="tick" aria-hidden="true">{n ? <Icon name="check" size={14} /> : null}</span>
                <span>
                  {d.label} {n > 1 && <span className="muted">· {n} files</span>}
                  <span className="muted small block">{d.hint}</span>
                </span>
                <span className="sr-only">{n ? "added" : "missing"}</span>
              </li>
            );
          })}
        </ul>
        <p className="muted small">
          The ACRA profile and the guarantors' Notices of Assessment are fetched from ACRA and IRAS. Add
          application.json and any invoices, purchase orders or bills of lading too.
        </p>
      </aside>

      <div className="upload-run">
        <p className="muted small" role="status">{waiting ?? "Every required document is here."}</p>
        <button type="button" className="btn primary" disabled={!ready || starting} onClick={() => onRun(held.map((h) => h.file))}>
          {starting ? "Starting…" : held.length ? `Run ${held.length} file${held.length === 1 ? "" : "s"}` : "Run"}
        </button>
      </div>
    </section>
  );
}
