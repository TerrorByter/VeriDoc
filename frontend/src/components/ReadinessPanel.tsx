import type { Readiness } from "../types";

const tone = (status: string) =>
  status === "Provided" ? "ok" : status === "Not checked in prototype" ? "muted" : "warn";

export function ReadinessPanel({ readiness }: { readiness: Readiness }) {
  return (
    <section className="card" aria-labelledby="ready-title">
      <h2 id="ready-title">
        Application readiness{" "}
        <span className={`status ${readiness.status === "Complete" ? "ok" : "warn"}`}>
          {readiness.status}
        </span>
      </h2>
      <ul className="checklist">
        {readiness.checklist.map((item) => (
          <li key={item.id}>
            <span className={`dot ${tone(item.status)}`} aria-hidden="true" />
            <span>{item.label}</span>
            <span className="muted">{item.status}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
