"""
gldb/signal_library.py
----------------------
The fraud signal library: every check mapped to a named, versioned signal.

Checks find evidence; this library says what each finding means to an
analyst, how much it counts, and what would resolve it. Keeping it in one
reviewable place (a rule catalogue) means risk logic is not scattered through
code, and every score line can name the rule and RULE_VERSION behind it.

Signal families:
  DOC  document integrity and forensics   FIN  financial arithmetic
  TXN  transaction consistency            REG  registry and identity
  ENT  entity relationships               TEMP temporal consistency
  COMP compliance and completeness        SYS  system health

Bump RULE_VERSION whenever a signal is added, removed or re-weighted.
"""

from __future__ import annotations

from gldb.schemas import Signal

RULE_VERSION = "2026-10-21.2"

SIGNALS: tuple[Signal, ...] = (
    # DOC: document integrity and forensics -------------------------------
    Signal(signal_id="DOC-001", check_id="FORENSIC_HIDDEN_TEXT", family="DOC",
           name="Hidden text in document",
           remedy="Obtain the original file directly from the issuer and ask the applicant to explain the hidden text."),
    Signal(signal_id="DOC-002", check_id="FORENSIC_MIXED_FONT", family="DOC",
           name="Figure edited over the original",
           remedy="Request the original document or a certified copy from the issuer."),
    Signal(signal_id="DOC-003", check_id="FORENSIC_INCREMENTAL_UPDATE", family="DOC",
           name="PDF saved again after creation",
           remedy="Request the document as first issued, or confirm the later edit with the issuer."),
    Signal(signal_id="DOC-004", check_id="FORENSIC_OCR_MISMATCH", family="DOC",
           name="Visible figures differ from the file's text",
           remedy="Compare the page with the original from the issuer; confirm which figure is genuine."),
    Signal(signal_id="DOC-005", check_id="META_MOD_AFTER_CREATE", family="DOC",
           name="Modified long after creation",
           remedy="Ask the applicant why the document was edited after it was issued."),
    Signal(signal_id="DOC-006", check_id="META_PRODUCER_MISMATCH", family="DOC",
           name="Produced by different software from the rest of the pack",
           remedy="Confirm the document came from the issuer's own system."),
    Signal(signal_id="DOC-007", check_id="FORENSIC_OCR_SKIPPED", family="DOC",
           name="Visible-vs-file comparison not run",
           remedy="Re-run the case with OCR turned on."),
    Signal(signal_id="DOC-008", check_id="PROMPT_INJECTION", family="DOC",
           name="Instructions aimed at an AI model",
           remedy="Treat the document as untrusted: obtain it directly from the issuer and check "
                  "each figure against the original by hand."),
    Signal(signal_id="DOC-009", check_id="PROMPT_SCREEN_SKIPPED", family="DOC",
           name="Prompt attack screen not run",
           remedy="Re-run the case with PROMPT_SCREEN_ENABLED on."),
    # FIN: financial arithmetic --------------------------------------------
    Signal(signal_id="FIN-001", check_id="INV_ARITH", family="FIN",
           name="Invoice arithmetic does not add up",
           remedy="Request a corrected or original invoice from the seller."),
    Signal(signal_id="FIN-002", check_id="FS_BALANCE", family="FIN",
           name="Balance sheet does not balance",
           remedy="Request audited financial statements."),
    Signal(signal_id="FIN-003", check_id="BANK_RUNNING_BAL", family="FIN",
           name="Bank running balance breaks",
           remedy="Obtain the statement directly from the bank."),
    Signal(signal_id="FIN-004", check_id="BANK_CONTINUITY", family="FIN",
           name="Statements do not chain month to month",
           remedy="Obtain the missing or original statements directly from the bank."),
    Signal(signal_id="FIN-005", check_id="FS_ACRA_MISMATCH", family="FIN",
           name="Submitted figures differ from ACRA's filing",
           remedy="Obtain the financial statements as filed with ACRA, and ask the applicant to explain the difference."),
    Signal(signal_id="FIN-006", check_id="XDOC_BANK_CASH", family="FIN",
           name="Bank balance exceeds the balance-sheet cash",
           remedy="Ask the applicant to reconcile the account to the balance sheet; obtain the statement directly from the bank."),
    Signal(signal_id="FIN-007", check_id="XDOC_BANK_DEBT", family="FIN",
           name="Debt payments not supported by the balance sheet",
           remedy="Request the loan or bond agreement and the lender's statement; ask why the debt is not on the balance sheet."),
    Signal(signal_id="FIN-008", check_id="XDOC_BANK_REVENUE", family="FIN",
           name="Bank receipts out of line with reported revenue",
           remedy="Ask the applicant to reconcile revenue to bank receipts; request more statements or the sales ledger."),
    # TXN: transaction consistency -----------------------------------------
    Signal(signal_id="TXN-001", check_id="XDOC_INVOICE_BANK", family="TXN",
           name="Invoice not matched by a bank receipt",
           remedy="Request proof of payment, or the bank statement showing the receipt."),
    Signal(signal_id="TXN-002", check_id="XDOC_PO_INVOICE", family="TXN",
           name="Invoice not supported by a purchase order",
           remedy="Request the buyer's purchase order or confirm the order with the buyer."),
    Signal(signal_id="TXN-003", check_id="XDOC_BL_INVOICE", family="TXN",
           name="Invoiced quantity exceeds quantity shipped",
           remedy="Obtain shipping records from the carrier and confirm receipt with the consignee."),
    # REG: registry and identity -------------------------------------------
    Signal(signal_id="REG-001", check_id="XDOC_IDENTITY", family="REG",
           name="Company identity inconsistent across documents",
           remedy="Verify the company's UEN and name against ACRA; ask the applicant to explain."),
    Signal(signal_id="REG-002", check_id="REGISTRY_ENTITY_NOT_FOUND", family="REG",
           name="Company not in the company register",
           remedy="Search ACRA directly; ask the applicant for the correct UEN and BizFile extract."),
    Signal(signal_id="REG-003", check_id="REGISTRY_NAME_MISMATCH", family="REG",
           name="Name differs from the registered name",
           remedy="Compare with the ACRA BizFile extract; ask the applicant to explain the difference."),
    Signal(signal_id="REG-004", check_id="REGISTRY_ENTITY_INACTIVE", family="REG",
           name="Company not live in the register",
           remedy="Confirm the company's status with ACRA before any further step."),
    Signal(signal_id="REG-005", check_id="REGISTRY_SIGNATORY_NOT_OFFICER", family="REG",
           name="Signatory not a registered officer",
           remedy="Request the board resolution authorising the signatory, and verify identity."),
    Signal(signal_id="REG-006", check_id="XDOC_ACRA_ENTITY", family="REG",
           name="Entity form differs from the ACRA profile",
           remedy="Compare the form with the ACRA profile; ask the applicant to correct whichever is wrong."),
    Signal(signal_id="REG-007", check_id="XDOC_ACRA_SIGNATORY", family="REG",
           name="Form signatory not an officer in the ACRA profile",
           remedy="Request the board resolution authorising the signatory, and verify identity."),
    Signal(signal_id="REG-008", check_id="XDOC_ACRA_PERSON", family="REG",
           name="Person's details differ from the ACRA profile",
           remedy="Verify the person's identity and address with them; ask which document is out of date."),
    Signal(signal_id="REG-009", check_id="XDOC_OFFICIAL_RECORD", family="REG",
           name="Uploaded copy differs from the official record",
           remedy="Treat the uploaded copy as altered; rely on the record fetched from ACRA or IRAS and ask the applicant why they differ."),
    Signal(signal_id="REG-010", check_id="OFFICIAL_RECORDS_UNAVAILABLE", family="REG",
           name="ACRA and IRAS records not fetched",
           remedy="Re-run the case once the official records service answers."),
    # TEMP: temporal consistency ------------------------------------------
    Signal(signal_id="TEMP-001", check_id="TEMP_BEFORE_INCORPORATION", family="TEMP",
           name="Dated before the company existed",
           remedy="Check the incorporation date with ACRA; ask the applicant which entity issued "
                  "or received the document."),
    Signal(signal_id="TEMP-002", check_id="TEMP_TRADE_SEQUENCE", family="TEMP",
           name="Trade steps out of order",
           remedy="Ask the buyer to confirm when the order was placed; request the original "
                  "order email or the buyer's procurement record."),
    # COMP: compliance and completeness (never scored; drive readiness) ----
    Signal(signal_id="COMP-001", check_id="BANK_COVERAGE", family="COMP",
           name="Bank statements do not cover the required period",
           remedy="Request the missing months of bank statements."),
    Signal(signal_id="COMP-002", check_id="CHECKLIST_MISSING_DOC", family="COMP",
           name="Required document missing",
           remedy="Request the missing document."),
    Signal(signal_id="COMP-003", check_id="MANIFEST_MISSING", family="COMP",
           name="Application data missing",
           remedy="Obtain the online application data (application.json)."),
    Signal(signal_id="COMP-010", check_id="FORM_ENT_MANDATORY", family="COMP",
           name="FATCA/CRS form: mandatory field empty",
           remedy="Return the form to the applicant to complete."),
    Signal(signal_id="COMP-011", check_id="FORM_ENT_MAILING", family="COMP",
           name="FATCA/CRS form: mailing address issue",
           remedy="Ask the applicant to complete or explain the mailing address."),
    Signal(signal_id="COMP-012", check_id="FORM_ENT_BRANCH", family="COMP",
           name="FATCA/CRS form: branch details issue",
           remedy="Ask the applicant to complete the branch section."),
    Signal(signal_id="COMP-013", check_id="FORM_ENT_US_STATUS", family="COMP",
           name="FATCA/CRS form: US status incomplete",
           remedy="Ask the applicant to declare US status; request a W-8 or W-9 where needed."),
    Signal(signal_id="COMP-014", check_id="FORM_ENT_TIN_OR_REASON", family="COMP",
           name="FATCA/CRS form: tax number or reason missing",
           remedy="Ask the applicant for each tax number or the reason one is not available."),
    Signal(signal_id="COMP-015", check_id="FORM_ENT_TYPE_SELECTION", family="COMP",
           name="FATCA/CRS form: entity type selection invalid",
           remedy="Ask the applicant to select exactly one entity type."),
    Signal(signal_id="COMP-016", check_id="FORM_ENT_DECLARATION", family="COMP",
           name="FATCA/CRS form: declaration incomplete",
           remedy="Ask the applicant to sign and date the declaration."),
    Signal(signal_id="COMP-017", check_id="FORM_TEMPLATE_MATCH", family="COMP",
           name="FATCA/CRS form is not GLDB's current template",
           remedy="Ask the applicant to complete the current GLDB form."),
    Signal(signal_id="COMP-018", check_id="FORM_ENT_NAME_MATCH", family="COMP",
           name="FATCA/CRS form: name differs from application",
           remedy="Ask the applicant to explain or correct the legal name."),
    Signal(signal_id="COMP-019", check_id="FORM_ENT_ADDRESS_MATCH", family="COMP",
           name="FATCA/CRS form: address differs from application",
           remedy="Ask the applicant to explain or correct the address."),
    Signal(signal_id="COMP-020", check_id="FORM_ENT_INCORPORATION", family="COMP",
           name="FATCA/CRS form: incorporation details differ",
           remedy="Verify incorporation details against ACRA."),
    Signal(signal_id="COMP-021", check_id="FORM_ENT_SG_TIN", family="COMP",
           name="FATCA/CRS form: Singapore tax number differs from UEN",
           remedy="Ask the applicant to correct the Singapore tax number."),
    Signal(signal_id="COMP-022", check_id="FORM_ENT_START_UP", family="COMP",
           name="FATCA/CRS form: start-up classification unreasonable",
           remedy="Ask the applicant to confirm the entity classification."),
    Signal(signal_id="COMP-023", check_id="FORM_ENT_SIGNATORY", family="COMP",
           name="FATCA/CRS form: signatory not authorised",
           remedy="Verify the signatory against the board resolution."),
    Signal(signal_id="COMP-030", check_id="SANCTIONS_MATCH", family="COMP",
           name="Possible sanctions list match",
           remedy="Compare identifiers (date of birth, registration number, address) with the list "
                  "entry; escalate to the MLRO if not ruled out."),
    # SYS: system health ---------------------------------------------------
    Signal(signal_id="SYS-001", check_id="SYSTEM_CHECK_ERROR", family="SYS",
           name="A check failed to run",
           remedy="Verify the affected area manually; report the error."),
)

#: check_id -> Signal
BY_CHECK: dict[str, Signal] = {s.check_id: s for s in SIGNALS}


def signal_for(check_id: str) -> Signal:
    """The signal for *check_id*. A check missing from the library still
    scores, labelled UNMAPPED, so a new check can never silently vanish
    from the score (tests require every real check to be mapped)."""
    return BY_CHECK.get(check_id) or Signal(
        signal_id="UNMAPPED", check_id=check_id, family="SYS",
        name=check_id, remedy="Add this check to the signal library.",
    )
