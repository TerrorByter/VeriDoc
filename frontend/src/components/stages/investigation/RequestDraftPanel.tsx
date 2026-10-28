import { useState } from "react";
import { getRequestDraft } from "../../../api";
import type { RequestDraft } from "../../../types";
import { ErrorNote } from "../../ui";
import { Section } from "../common";

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** A request for information drafted from the case, for the analyst to edit and send through the bank's own
 * channel. Screening, forensic and money-laundering findings are never in it: the applicant is not told it was
 * screened or is suspected (tipping-off). */
export function RequestDraftPanel({ caseId }: { caseId: string }) {
  const [draft, setDraft] = useState<RequestDraft | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const make = () => {
    setBusy(true);
    setError(null);
    getRequestDraft(caseId)
      .then((d) => {
        setDraft(d);
        setText(d.text);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const copy = () => {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
      })
      .catch(() => setError("Could not copy: select the text and copy it instead."));
  };

  return (
    <Section title="Request for information" aside="A draft to edit; nothing is sent">
      {error && <ErrorNote>{error}</ErrorNote>}
      {!draft ? (
        <button type="button" className="btn" disabled={busy} onClick={make}>
          {busy ? "Drafting…" : "Draft request for information"}
        </button>
      ) : !draft.text ? (
        <p className="muted">Nothing to ask: no open finding needs anything from the applicant.</p>
      ) : (
        <>
          <p className="muted small">
            {plural(draft.items.length, "request")} from rule findings
            {draft.questions.length > 0 && `, ${plural(draft.questions.length, "verified AI question")}`}.
            {draft.withheld > 0 && ` ${plural(draft.withheld, "finding")} stay internal: screening, forensic and money-laundering findings are never shared with the applicant.`}
          </p>
          <label className="field">
            <span className="sr-only">Draft request</span>
            <textarea id="rfi-draft" rows={14} value={text} onChange={(e) => setText(e.target.value)} />
          </label>
          <div className="chips">
            <button type="button" className="btn primary" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
            <button type="button" className="btn" onClick={() => setText(draft.text)} disabled={text === draft.text}>
              Reset to draft
            </button>
          </div>
        </>
      )}
    </Section>
  );
}
