import { DOC_TYPE_LABEL } from "../../catalogue";
import type { StageProps } from "../workspace/StageDetail";
import { Pill } from "../ui";
import { Section } from "./common";

export function IntakeStage({ kase, onView }: StageProps) {
  return (
    <Section title="Documents received" aside="SHA-256 of each file, as assessed">
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th scope="col">File</th>
              <th scope="col">Type</th>
              <th scope="col" className="num">Pages</th>
              <th scope="col">Fingerprint</th>
            </tr>
          </thead>
          <tbody>
            {kase.docs.map((d) => (
              <tr key={d.doc_id}>
                <td>
                  <button type="button" className="link mono" onClick={() => onView({ docId: d.doc_id, page: 1 })}>
                    {d.filename}
                  </button>
                </td>
                <td>
                  {d.doc_type === "unknown" ? <Pill tone="med">Unrecognised</Pill> : DOC_TYPE_LABEL[d.doc_type] ?? d.doc_type}
                </td>
                <td className="num">{d.page_count}</td>
                <td><code title={d.sha256}>{d.sha256.slice(0, 12)}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}
