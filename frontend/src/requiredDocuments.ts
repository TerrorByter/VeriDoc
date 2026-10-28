/** The documents a loan application must include before it can be run. The server reads each file's type
 * from its content (POST /api/documents/identify), so the analyst drops them in any order. ACRA's and IRAS's
 * records are fetched, not uploaded. */
export const REQUIRED_DOCUMENTS: { type: string; label: string; hint: string }[] = [
  { type: "financial_statements", label: "Submitted financial statements", hint: "A workbook or a PDF" },
  { type: "bank_statement", label: "Bank statements", hint: "The latest six months; coverage is checked in the run" },
  { type: "fatca_crs_entity", label: "FATCA & CRS form (entity)", hint: "The company's self-certification" },
  { type: "fatca_crs_individual", label: "FATCA & CRS form (individual)", hint: "One per signatory and controlling person" },
];

/** File types the drop area offers; anything else is refused by the server's check */
export const ACCEPTED = ".pdf,.xlsx,.json";
