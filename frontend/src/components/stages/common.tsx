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

export function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="sub">
      <div className="sub-head">
        <h3>{title}</h3>
        {aside && <span className="muted small">{aside}</span>}
      </div>
      {children}
    </section>
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
