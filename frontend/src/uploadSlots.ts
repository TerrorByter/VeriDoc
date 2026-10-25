export interface UploadSlot {
  id: string;
  title: string;
  hint: string;
  /** File extensions the slot's file picker offers */
  accept: string;
  /** False when the case takes one such document: a new file replaces the old */
  multiple: boolean;
}

/** The documents of a loan application the applicant supplies, one slot each. A
 * slot only labels the upload for the officer: the server reads every file's type
 * from its content. ACRA's and IRAS's records are fetched, not uploaded. */
export const UPLOAD_SLOTS: UploadSlot[] = [
  {
    id: "financials", title: "Submitted financial statements", accept: ".xlsx,.pdf", multiple: false,
    hint: "Income statement, balance sheet and cash flow, as a workbook or a PDF.",
  },
  {
    id: "bank", title: "Bank statements", accept: ".pdf", multiple: true,
    hint: "The latest six months: one statement covering them, or one for each month.",
  },
  {
    id: "entity", title: "FATCA & CRS form (entity)", accept: ".pdf", multiple: false,
    hint: "The company's self-certification form.",
  },
  {
    id: "individual", title: "FATCA & CRS form (individual)", accept: ".pdf", multiple: true,
    hint: "One for each signatory and controlling person.",
  },
];

export const OTHER_SLOT: UploadSlot = {
  id: "other", title: "Other documents", accept: ".pdf,.xlsx,.json", multiple: true,
  hint: "The application data as application.json, invoices, purchase orders, or anything else. A copy of an ACRA profile or Notice of Assessment is checked against the original.",
};
