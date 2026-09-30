import { useEffect, useRef, useState } from "react";
import { listPacks } from "../api";

interface Props {
  busy: boolean;
  onUpload: (files: File[]) => void;
  onPack: (name: string) => void;
}

export function UploadPanel({ busy, onUpload, onPack }: Props) {
  const [packs, setPacks] = useState<string[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listPacks().then(setPacks).catch(() => setPacks([]));
  }, []);

  const add = (list: FileList | null) =>
    list && setFiles((prev) => [...prev, ...Array.from(list)]);

  return (
    <section className="card upload" aria-labelledby="upload-title">
      <h2 id="upload-title">Submit a loan document set</h2>
      <p className="muted">
        Upload the application's PDFs (invoices, bank statements, financial
        statements, FATCA/CRS form) and optionally its <code>application.json</code>.
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
        <button type="button" className="btn ghost" onClick={() => input.current?.click()}>
          Choose files
        </button>
        <span className="muted">or drop them here</span>
        <input
          ref={input}
          type="file"
          multiple
          hidden
          accept=".pdf,.json"
          aria-label="Loan documents"
          onChange={(e) => add(e.target.files)}
        />
      </div>

      {files.length > 0 && (
        <ul className="filelist" aria-label="Selected files">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`}>{f.name}</li>
          ))}
        </ul>
      )}

      <div className="row">
        <button
          type="button"
          className="btn primary"
          disabled={busy || files.length === 0}
          onClick={() => onUpload(files)}
        >
          {busy ? <><span className="spinner" aria-hidden="true" /> Analysing…</> : "Run analysis"}
        </button>
        {files.length > 0 && !busy && (
          <button type="button" className="btn ghost" onClick={() => setFiles([])}>
            Clear
          </button>
        )}
      </div>

      {packs.length > 0 && (
        <div className="demo">
          <h3>Or try a synthetic demo pack</h3>
          <div className="row">
            {packs.map((name) => (
              <button
                key={name}
                type="button"
                className="btn chip"
                disabled={busy}
                onClick={() => onPack(name)}
              >
                {name}
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
