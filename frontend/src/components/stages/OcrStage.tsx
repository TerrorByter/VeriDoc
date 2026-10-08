import type { StageProps } from "../workspace/StageDetail";
import { Meter, Metric, OffNote, Section } from "./common";
import { Findings } from "./Findings";

const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;

export function OcrStage(props: StageProps) {
  const { kase } = props;
  const rows = Object.entries(kase.ocr_confidence).filter(([, pages]) => pages.length > 0);
  const findings = kase.evidence.filter((e) => e.check_id.startsWith("FORENSIC_OCR") && e.severity !== "info");

  if (kase.stages.find((s) => s.stage_id === "ocr")?.status === "off") {
    return (
      <OffNote stage="ocr">
        Without OCR the visible-figure check (DOC-004) cannot run, and scanned pages have no text to check.
      </OffNote>
    );
  }

  const pages = rows.flatMap(([, p]) => p);
  const lowest = rows.length ? rows.reduce((lo, row) => (Math.min(...row[1]) < Math.min(...lo[1]) ? row : lo)) : null;

  return (
    <>
      <div className="metrics">
        <Metric label="Pages read" value={pages.length} note={`${rows.length} documents`} />
        <Metric label="Mean confidence" value={pages.length ? `${mean(pages).toFixed(1)}%` : "—"} />
        <Metric
          label="Lowest"
          value={lowest ? `${Math.min(...lowest[1]).toFixed(1)}%` : "—"}
          note={lowest ? <span className="mono">{lowest[0]}</span> : undefined}
        />
      </div>

      <Section title="Visible text compared with the file" aside="DOC-004">
        <Findings {...props} evidence={findings} empty="Visible figures match the file's own text on every page" />
      </Section>

      <Section title="Confidence by document" aside="Positions kept on the server for highlighting">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr><th scope="col">Document</th><th scope="col" className="num">Pages</th><th scope="col">Mean confidence</th></tr>
            </thead>
            <tbody>
              {rows.map(([doc, conf]) => (
                <tr key={doc}>
                  <td>
                    <button type="button" className="link mono" onClick={() => props.onView({ docId: doc, page: 1 })}>{doc}</button>
                  </td>
                  <td className="num">{conf.length}</td>
                  <td>
                    <span className="meter-cell">
                      <Meter value={(mean(conf) - 80) / 20} tone={mean(conf) < 90 ? "med" : "brand"} />
                      <span className="mono">{mean(conf).toFixed(1)}%</span>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">Bars start at 80%; forms are read field by field and are not OCR&apos;d.</p>
      </Section>
    </>
  );
}
