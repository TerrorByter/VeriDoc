import { useRef, useState, type ReactNode } from "react";
import { Icon } from "./ui";

/** A place to drop files, or to choose them with the button. The files go to onFiles; children
 * show what the area holds. */
export function DropArea({ accept, onFiles, children }: {
  accept: string;
  onFiles: (files: File[]) => void;
  children?: ReactNode;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  return (
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
        onFiles(Array.from(e.dataTransfer.files));
      }}
    >
      <Icon name="upload" size={20} />
      <p className="drop-title">Drop the application's files here</p>
      <p className="muted small">PDFs, the financial statements workbook and application.json, in any order</p>
      <button type="button" className="btn small" onClick={() => input.current?.click()}>Choose files</button>
      <input
        ref={input}
        type="file"
        multiple
        hidden
        accept={accept}
        aria-label="Choose the application's files"
        onChange={(e) => {
          onFiles(Array.from(e.target.files ?? []));
          e.target.value = "";
        }}
      />
      {children}
    </div>
  );
}
