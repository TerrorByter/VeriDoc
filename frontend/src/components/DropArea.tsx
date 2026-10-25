import { useRef, useState, type ReactNode } from "react";
import type { UploadSlot } from "../uploadSlots";
import { Icon } from "./ui";

/** A place to drop files, or to choose them with the button. The files go to onFiles; children
 * show what the area holds. */
export function DropArea({ slot, filled = false, onFiles, children }: {
  slot: UploadSlot;
  filled?: boolean;
  onFiles: (files: File[]) => void;
  children?: ReactNode;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const label = slot.multiple ? "Choose files" : filled ? "Replace" : "Choose file";

  return (
    <div
      className={`dropzone slot${dragging ? " dragging" : ""}${filled ? " filled" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        onFiles(Array.from(e.dataTransfer.files));
      }}
    >
      <div className="slot-head">
        <h3>{slot.title}</h3>
        <button type="button" className="btn small" onClick={() => input.current?.click()}>
          <Icon name="upload" size={14} />
          {label}
        </button>
        <input
          ref={input}
          id={`upload-${slot.id}`}
          type="file"
          multiple={slot.multiple}
          hidden
          accept={slot.accept}
          aria-label={slot.title}
          onChange={(e) => {
            onFiles(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
      </div>
      <p className="muted">{slot.hint}</p>
      {children}
    </div>
  );
}
