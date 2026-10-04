import type { StageProps } from "../workspace/StageDetail";
import { Pill } from "../ui";
import { OffNote, Section } from "./common";
import { Findings } from "./Findings";

export function PromptScreenStage(props: StageProps) {
  const { kase, onView } = props;
  const screened = kase.prompt_screen;
  if (screened === null) {
    return (
      <OffNote stage="prompt_screen">
        Instructions printed in a document for an AI reviewer are then caught only if hidden (DOC-001).
      </OffNote>
    );
  }
  const findings = kase.evidence.filter((e) => e.check_id === "PROMPT_INJECTION");
  const docs = kase.docs.filter((d) => d.doc_id in screened);

  return (
    <>
      <Section title="Prompt attacks" aside="DOC-008, a hard flag">
        <Findings {...props} evidence={findings} empty="No instructions aimed at an AI model in any page" />
      </Section>
      <Section title="Pages screened" aside="Forms are not screened: no model reads them">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr><th scope="col">Document</th><th scope="col" className="num">Pages</th><th scope="col">Result</th></tr>
            </thead>
            <tbody>
              {docs.map((d) => {
                const hits = screened[d.doc_id];
                return (
                  <tr key={d.doc_id}>
                    <td>
                      <button type="button" className="link mono" onClick={() => onView({ docId: d.doc_id, page: hits[0]?.page ?? 1 })}>
                        {d.filename}
                      </button>
                    </td>
                    <td className="num">{d.page_count}</td>
                    <td>
                      {hits.length ? (
                        hits.map((h) => (
                          <Pill key={h.page} tone="high">Page {h.page}, {h.confidence.toLowerCase()} confidence</Pill>
                        ))
                      ) : (
                        <span className="muted">Clear</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Section>
    </>
  );
}
