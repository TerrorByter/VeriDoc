import { useEffect, useState } from "react";
import { createCase, createCaseFromPack, listPacks } from "../../api";
import { navigate } from "../../route";
import type { PackInfo, RunStarted } from "../../types";
import { OTHER_SLOT, UPLOAD_SLOTS, type UploadSlot } from "../../uploadSlots";
import { DropArea } from "../DropArea";
import { ErrorNote, Icon } from "../ui";

const SLOTS = [...UPLOAD_SLOTS, OTHER_SLOT];

const extension = (file: File) => file.name.slice(file.name.lastIndexOf(".")).toLowerCase();

export function NewCaseScreen() {
  const [packs, setPacks] = useState<PackInfo[]>([]);
  const [held, setHeld] = useState<Record<string, File[]>>({});
  const [starting, setStarting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const files = SLOTS.flatMap((slot) => held[slot.id] ?? []);
  const typesAdded = UPLOAD_SLOTS.filter((slot) => held[slot.id]?.length).length;

  useEffect(() => {
    listPacks().then(setPacks).catch(() => setPacks([]));
  }, []);

  /** Put files in a slot. A file name is used once in the case: the server keeps files by name. */
  const add = (slot: UploadSlot, incoming: File[]) => {
    if (incoming.length === 0) return;
    const accepted = slot.accept.split(",");
    const wrong = incoming.filter((file) => !accepted.includes(extension(file)));
    if (wrong.length) return setError(`${wrong.map((f) => f.name).join(", ")}: ${slot.title} takes ${accepted.join(" or ")} files.`);
    if (!slot.multiple && incoming.length > 1) return setError(`${slot.title} takes one file.`);
    const kept = slot.multiple ? (held[slot.id] ?? []) : [];
    const taken = new Set([...SLOTS.filter((s) => s.id !== slot.id).flatMap((s) => held[s.id] ?? []), ...kept].map((f) => f.name));
    const repeated = incoming.filter((file) => taken.has(file.name));
    if (repeated.length) return setError(`Already added: ${repeated.map((f) => f.name).join(", ")}.`);
    setError(null);
    setHeld({ ...held, [slot.id]: [...kept, ...incoming] });
  };

  const remove = (slot: UploadSlot, file: File) =>
    setHeld({ ...held, [slot.id]: (held[slot.id] ?? []).filter((f) => f !== file) });

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
        <p className="muted">
          Synthetic applicants in the format of the team's sample, each built to show one kind of risk.
        </p>
        <ul className="pack-grid">
          {packs.map(({ name, note }) => (
            <li key={name}>
              <button
                type="button"
                className="pack"
                disabled={starting !== null}
                onClick={() => start(name, () => createCaseFromPack(name))}
              >
                <span className="pack-name mono">{name}</span>
                <span className="muted">{note ?? "Synthetic document set."}</span>
                {starting === name && <span className="spinner" aria-label="Starting" />}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel" aria-labelledby="upload-title">
        <h2 id="upload-title">Upload documents</h2>
        <p className="muted">
          Add each document of the application to its place. The pipeline reads what a file contains, not
          where it was put. The ACRA profile and the guarantors' Notices of Assessment are not uploaded: the
          pipeline fetches them from ACRA and IRAS.
        </p>
        <ul className="slot-grid">
          {UPLOAD_SLOTS.map((slot) => (
            <li key={slot.id}>
              <Slot slot={slot} files={held[slot.id] ?? []} onFiles={add} onRemove={remove} />
            </li>
          ))}
        </ul>
        <Slot slot={OTHER_SLOT} files={held[OTHER_SLOT.id] ?? []} onFiles={add} onRemove={remove} />
        <div className="actions">
          <span className="muted slot-count">
            {typesAdded} of {UPLOAD_SLOTS.length} document types added
          </span>
          {files.length > 0 && (
            <button type="button" className="btn ghost" disabled={starting !== null} onClick={() => setHeld({})}>
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

function Slot({ slot, files, onFiles, onRemove }: {
  slot: UploadSlot;
  files: File[];
  onFiles: (slot: UploadSlot, files: File[]) => void;
  onRemove: (slot: UploadSlot, file: File) => void;
}) {
  return (
    <DropArea slot={slot} filled={files.length > 0} onFiles={(incoming) => onFiles(slot, incoming)}>
      {files.length > 0 && (
        <ul className="file-list" aria-label={`${slot.title}: selected files`}>
          {files.map((file) => (
            <li key={file.name}>
              <Icon name="doc" size={14} />
              <span className="mono">{file.name}</span>
              <span className="muted">{Math.max(1, Math.round(file.size / 1024))} KB</span>
              <button type="button" className="btn ghost small" aria-label={`Remove ${file.name}`}
                onClick={() => onRemove(slot, file)}>
                <Icon name="close" size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </DropArea>
  );
}
