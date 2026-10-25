import type { AcraFinancials, AcraRecord } from "../../types";
import type { StageProps } from "../workspace/StageDetail";
import { ErrorNote, Pill } from "../ui";
import { OffNote, Section } from "./common";
import { Findings } from "./Findings";

const FILED: [keyof AcraFinancials, string][] = [
  ["revenue", "Revenue"], ["profit_after_tax", "Profit after tax"], ["total_assets", "Total assets"],
  ["total_liabilities", "Total liabilities"], ["operating_cashflow", "Operating cash flow"],
];

const sgd = (amount: string | null) =>
  amount == null ? "—" : Number(amount).toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const live = (record: AcraRecord) => record.status.toUpperCase().startsWith("LIVE");

function Applicant({ record }: { record: AcraRecord }) {
  const filed = record.financials;
  return (
    <>
      <dl className="facts">
        <div><dt>Company</dt><dd>{record.company_name}</dd></div>
        <div><dt>UEN</dt><dd className="mono">{record.uen}</dd></div>
        <div><dt>Status</dt><dd><Pill tone={live(record) ? "low" : "crit"}>{record.status}</Pill></dd></div>
        <div><dt>Incorporated</dt><dd className="mono">{record.incorporation_date}</dd></div>
        <div><dt>Registered office</dt><dd>{record.registered_address}</dd></div>
      </dl>
      <div className="table-wrap">
        <table className="data">
          <caption className="muted small">Officers and shareholders</caption>
          <thead>
            <tr><th scope="col">Name</th><th scope="col">Role</th><th scope="col">NRIC/FIN</th><th scope="col">Since</th></tr>
          </thead>
          <tbody>
            {record.officers.map((o) => (
              <tr key={o.id_number + o.position}>
                <td>{o.name}</td><td>{o.position}</td><td className="mono">{o.id_number}</td>
                <td className="mono">{o.appointed}</td>
              </tr>
            ))}
            {record.shareholders.map((h) => (
              <tr key={h.name}>
                <td>{h.name}</td><td>Shareholder</td><td className="muted">—</td>
                <td className="mono">{h.shares.toLocaleString("en-SG")} shares</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {filed && (
        <dl className="facts">
          {FILED.map(([key, label]) => (
            <div key={key}>
              <dt>{label}, year to {filed.period_end}</dt>
              <dd className="mono">{filed.currency} {filed[key] ?? "—"}m</dd>
            </div>
          ))}
        </dl>
      )}
    </>
  );
}

export function RecordsStage(props: StageProps) {
  const { kase } = props;
  const records = kase.official;
  if (records === null) {
    return <OffNote stage="records">The documents are held to an uploaded ACRA profile and notices, if any.</OffNote>;
  }
  const findings = kase.evidence.filter((e) => ["OFFICIAL_RECORDS_UNAVAILABLE", "XDOC_OFFICIAL_RECORD"].includes(e.check_id));
  const applicant = records.applicant_uen ? records.companies[records.applicant_uen] : null;
  const others = Object.entries(records.companies).filter(([uen]) => uen !== records.applicant_uen);
  const fetched = new Date(records.retrieved_at).toLocaleString("en-SG");

  return (
    <>
      {records.error && <ErrorNote>ACRA and IRAS did not answer: {records.error}</ErrorNote>}
      <Section title="Uploaded copies against the originals" aside="REG-009, REG-010">
        <Findings {...props} evidence={findings}
          empty="No uploaded ACRA profile or notice differs from the record it copies" />
      </Section>
      <Section title="The applicant at ACRA" primary
        aside={`${records.versions.acra ?? "ACRA"}, fetched ${fetched}`}>
        {applicant ? <Applicant record={applicant} />
          : <p className="muted">
              {records.applicant_uen ? `ACRA has no company with UEN ${records.applicant_uen}.` : "No document gives the applicant's UEN."}
            </p>}
      </Section>
      {others.length > 0 && (
        <Section title="Other companies the documents name">
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th scope="col">UEN</th><th scope="col">Name at ACRA</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {others.map(([uen, record]) => (
                  <tr key={uen}>
                    <td className="mono">{uen}</td>
                    <td>{record?.company_name ?? <span className="muted">Not registered</span>}</td>
                    <td>{record ? <Pill tone={live(record) ? "low" : "crit"}>{record.status}</Pill> : <Pill tone="crit">No record</Pill>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
      <Section title="Notices of Assessment from IRAS" aside={records.versions.iras ?? "IRAS"}>
        {records.notices.length === 0 && records.no_notice.length === 0 ? (
          <p className="muted">No guarantor or declarant to fetch a notice for.</p>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Person</th><th scope="col">NRIC/FIN</th><th scope="col" className="num">Year</th>
                  <th scope="col" className="num">Income (SGD)</th><th scope="col" className="num">Chargeable</th>
                  <th scope="col" className="num">Tax payable</th>
                </tr>
              </thead>
              <tbody>
                {records.notices.map((n) => (
                  <tr key={n.id_number}>
                    <td>{n.name}</td><td className="mono">{n.id_number}</td>
                    <td className="num">{n.year_of_assessment}</td><td className="num mono">{sgd(n.income)}</td>
                    <td className="num mono">{sgd(n.chargeable_income)}</td><td className="num mono">{sgd(n.tax_payable)}</td>
                  </tr>
                ))}
                {records.no_notice.map((id) => (
                  <tr key={id}>
                    <td className="muted">No notice at IRAS</td><td className="mono">{id}</td>
                    <td colSpan={4} className="muted">Needs an uploaded notice, or the person's consent to fetch it</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  );
}
