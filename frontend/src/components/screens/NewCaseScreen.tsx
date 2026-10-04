import { useEffect, useRef, useState } from "react";
import { createCase, createCaseFromPack, listPacks } from "../../api";
import { navigate } from "../../route";
import type { RunStarted } from "../../types";
import { ErrorNote, Icon } from "../ui";

/** What each synthetic demo pack shows, so a presenter can pick the right one. */
const PACK_NOTES: Record<string, string> = {
  clean: "A genuine application. Expect a fast track.",
  incomplete: "A bank statement month is missing. Expect an incomplete case.",
  injected: "An invoice carries printed instructions aimed at an AI reviewer.",
  retro_po: "A purchase order dated after its invoice and shipment.",
  sanctioned: "A counterparty on the sanctions list.",
  scanned: "Scanned pages with no text layer. Needs OCR.",
  tampered: "Seven kinds of tampering across the pack.",
};

export function NewCaseScreen() {
  const [packs, setPacks] = useState<string[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [starting, setStarting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listPacks().then(setPacks).catch(() => setPacks([]));
  }, []);

  const add = (list: FileList | null) => list && setFiles((prev) => [...prev, ...Array.from(list)]);

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
          <p className="muted">Run a document set through the pipeline. You can follow each stage as it runs.</p>
        </div>
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}

      <section className="panel" aria-labelledby="demo-title">
        <h2 id="demo-title">Start from a demo pack</h2>
        <p className="muted">Synthetic applicants, each built to show one kind of risk.</p>
        <ul className="pack-grid">
          {packs.map((name) => (
            <li key={name}>
              <button
                type="button"
                className="pack"
                disabled={starting !== null}
                onClick={() => start(name, () => createCaseFromPack(name))}
              >
                <span className="pack-name mono">{name}</span>
                <span className="muted">{PACK_NOTES[name] ?? "Synthetic document set."}</span>
                {starting === name && <span className="spinner" aria-label="Starting" />}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel" aria-labelledby="upload-title">
        <h2 id="upload-title">Upload documents</h2>
        <p className="muted">
          Invoices, purchase orders, bills of lading, bank statements, financial statements and the
          FATCA/CRS form as PDFs, plus the application data as <code>application.json</code>.
        </p>
        <div
          className={`dropzone${dragging ? " dragging" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            add(e.dataTransfer.files);
          }}
        >
          <Icon name="upload" size={20} />
          <span>Drop files here or</span>
          <button type="button" className="btn" onClick={() => input.current?.click()}>Choose files</button>
          <input
            ref={input}
            id="upload-files"
            type="file"
            multiple
            hidden
            accept=".pdf,.json"
            aria-label="Loan documents"
            onChange={(e) => add(e.target.files)}
          />
        </div>
        {files.length > 0 && (
          <ul className="file-list" aria-label="Selected files">
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`}>
                <Icon name="doc" size={14} />
                <span className="mono">{f.name}</span>
                <span className="muted">{Math.max(1, Math.round(f.size / 1024))} KB</span>
              </li>
            ))}
          </ul>
        )}
        <div className="actions">
          {files.length > 0 && (
            <button type="button" className="btn ghost" disabled={starting !== null} onClick={() => setFiles([])}>
              Clear
            </button>
          )}
          <button
            type="button"
            className="btn primary"
            disabled={starting !== null || files.length === 0}
            onClick={() => start("upload", () => createCase(files))}
          >
            {starting === "upload"
              ? "Starting…"
              : files.length
                ? `Run ${files.length} file${files.length === 1 ? "" : "s"}`
                : "Run files"}
          </button>
        </div>
      </section>
    </main>
  );
}
