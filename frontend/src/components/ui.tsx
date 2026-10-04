import type { ReactNode } from "react";
import { PATH_LABEL, STATUS_LABEL, type Service } from "../catalogue";
import type { Band, CaseSummary, ReviewPath, Severity, Verdict } from "../types";

export type Tone = "low" | "med" | "high" | "crit" | "neutral" | "brand";

export function Pill({ tone, children, title }: { tone: Tone; children: ReactNode; title?: string }) {
  return <span className={`pill ${tone}`} title={title}>{children}</span>;
}

export const BAND_TONE: Record<Band, Tone> = { Low: "low", Medium: "med", High: "high" };

export const SEVERITY_TONE: Record<Severity, Tone> = {
  info: "neutral", low: "low", medium: "med", high: "high", critical: "crit",
};

export const VERDICT_TONE: Record<Verdict, Tone> = {
  corroborated: "high", possibly_innocent: "low", unclear: "neutral",
};

export const PATH_TONE: Record<ReviewPath, Tone> = {
  fast_track: "low", standard_review: "brand", enhanced_review: "high",
};

const STATUS_TONE: Record<CaseSummary["status"], Tone> = {
  running: "brand", failed: "crit", assessed: "brand", info_requested: "neutral",
  escalated: "crit", mlro_reviewed: "med", approved: "low", declined: "neutral",
};

export function StatusPill({ status }: { status: CaseSummary["status"] }) {
  return <Pill tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Pill>;
}

export function PathPill({ path }: { path: ReviewPath }) {
  return <Pill tone={PATH_TONE[path]}>{PATH_LABEL[path]}</Pill>;
}

export function ServiceTag({ service, off = false }: { service: Service; off?: boolean }) {
  return (
    <span className={`svc${off ? " off" : ""}`} title={off ? `${service} is switched off` : `Calls Amazon ${service}`}>
      {service}{off && " off"}
    </span>
  );
}

const PATHS = {
  check: "M5 8.3l2 2 4-4.3",
  alert: "M8 4.6v4.1M8 11.1v.2",
  dash: "M5 8h6",
  doc: "M4 1.5h5.5L12 4v10.5H4z M9.5 1.5V4H12",
  close: "M4 4l8 8M12 4l-8 8",
  moon: "M13 9.5A5.5 5.5 0 016.5 3a5.5 5.5 0 106.5 6.5z",
  sun: "M8 5a3 3 0 100 6 3 3 0 000-6z M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3 3l1 1M12 12l1 1M3 13l1-1M12 4l1-1",
  upload: "M8 11V3M5 6l3-3 3 3M3 13h10",
  left: "M10 3L5 8l5 5",
  right: "M6 3l5 5-5 5",
  external: "M9 3h4v4M13 3L7 9M11 9.5V13H3V5h3.5",
} as const;

export type IconName = keyof typeof PATHS | "done" | "flag" | "off" | "pending";

/** Small line icons; stroke follows the text colour. */
export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  const ring = name === "done" || name === "flag" || name === "off" || name === "pending";
  const inner = { done: PATHS.check, flag: PATHS.alert, off: PATHS.dash, pending: "" } as Record<string, string>;
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true" className="icon">
      {ring && (
        <circle cx="8" cy="8" r="6.6" stroke="currentColor" strokeWidth="1.4"
          strokeDasharray={name === "off" ? "2.4 2.4" : undefined} />
      )}
      <path
        d={ring ? inner[name] : PATHS[name as keyof typeof PATHS]}
        stroke="currentColor"
        strokeWidth={name === "flag" ? 1.8 : 1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return <p className="error-note" role="alert">{children}</p>;
}
