import type { ReactNode } from "react";
import type { StageId } from "../../types";

/** The server setting that switches each optional stage on. */
const SWITCH: Partial<Record<StageId, string>> = {
  ocr: "OCR_ENABLED=true",
  prompt_screen: "PROMPT_SCREEN_ENABLED=true",
  extract: "EXTRACTION_MODE=llm",
  ai_team: "AGENTS_ENABLED=true",
};

export function OffNote({ stage, children }: { stage: StageId; children?: ReactNode }) {
  return (
    <div className="off-note">
      <strong>Switched off for this run</strong>
      <p>
        {children} Turn it on with <code>{SWITCH[stage]}</code> in the server configuration and run the
        case again.
      </p>
    </div>
  );
}

/** A section of a stage. The primary ones carry what the analyst came for and lead with a larger heading. */
export function Section({ title, aside, primary = false, children }: {
  title: string;
  aside?: ReactNode;
  primary?: boolean;
  children: ReactNode;
}) {
  return (
    <section className={`sub${primary ? " primary" : ""}`}>
      <div className="sub-head">
        <h3>{title}</h3>
        {aside && <span className="muted small">{aside}</span>}
      </div>
      {children}
    </section>
  );
}

/** A section folded away until asked for: audit material, never hidden. The summary is the heading. */
export function Fold({ title, aside, open, onToggle, children }: {
  title: string;
  aside?: ReactNode;
  /** Controlled open state, for a fold something else (a citation) has to open */
  open?: boolean;
  onToggle?: (open: boolean) => void;
  children: ReactNode;
}) {
  return (
    <details className="fold" open={open} onToggle={onToggle && ((e) => onToggle(e.currentTarget.open))}>
      <summary>
        <h3>{title}</h3>
        {aside && <span className="muted small">{aside}</span>}
      </summary>
      <div className="fold-body">{children}</div>
    </details>
  );
}

export function Metric({ label, value, note }: { label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <div className="metric">
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
      {note && <span className="muted small">{note}</span>}
    </div>
  );
}

export function Meter({ value, tone = "brand" }: { value: number; tone?: string }) {
  return (
    <span className="meter" aria-hidden="true">
      <i className={tone} style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
    </span>
  );
}
