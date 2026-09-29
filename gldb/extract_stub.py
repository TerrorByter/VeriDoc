"""
gldb/extract_stub.py
--------------------
Deterministic parser for the GLDB synthetic pack layout produced by
gldb/data_factory.py.

Rendering contract (must match data_factory.py exactly):
- A4 page, Helvetica 10pt, 14pt line spacing, top-to-bottom.
- Label lines: "Label: value"  (money as "SGD 1,234.56")
- Table rows: cells separated by " | "  (money no SGD prefix; empty cell = "-")
- First line of every document is its title.

This module also handles FATCA/CRS AcroForms via gldb/form_maps.py.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pypdf

from gldb.config import money
from gldb.form_maps import ENTITY_FIELD_MAP
from gldb.intake import parse_pdf_date, read_text
from gldb.schemas import (
    Address,
    ApplicationManifest,
    BankStatement,
    ControllingPerson,
    DocMeta,
    EntitySelfCert,
    FinancialStatementSummary,
    Invoice,
    LineItem,
    Signatory,
    TaxResidencyRow,
    Transaction,
)


# ---------------------------------------------------------------------------
# Money parsing helper
# ---------------------------------------------------------------------------

def _parse_money(s: str) -> Decimal:
    """Parse a money string like "1,234.56" or "SGD 1,234.56" → Decimal."""
    s = s.strip().lstrip("SGD").replace(",", "").strip()
    return money(s)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s.strip())


# ---------------------------------------------------------------------------
# Invoice parser
# ---------------------------------------------------------------------------

def _parse_invoice(doc_id: str, pages: list[str]) -> Invoice:
    text = pages[0] if pages else ""
    lines = [l for l in text.splitlines() if l.strip()]

    def _get(label: str) -> str:
        for l in lines:
            if l.startswith(f"{label}:"):
                return l.split(":", 1)[1].strip()
        return ""

    seller = _get("Seller")
    seller_uen = _get("Seller UEN")
    buyer = _get("Buyer")
    number = _get("Invoice No")
    inv_date = _parse_date(_get("Invoice Date"))

    # Parse line items table
    line_items: list[LineItem] = []
    in_table = False
    source_quotes: dict[str, str] = {}
    for l in lines:
        if l.startswith("Description | Qty"):
            in_table = True
            continue
        if in_table:
            if l.startswith("Subtotal:"):
                break
            parts = [p.strip() for p in l.split(" | ")]
            if len(parts) == 4:
                desc, qty_s, up_s, amt_s = parts
                line_items.append(LineItem(
                    description=desc,
                    quantity=int(qty_s),
                    unit_price=_parse_money(up_s),
                    amount=_parse_money(amt_s),
                ))

    subtotal = _parse_money(_get("Subtotal"))
    gst = _parse_money(_get("GST (9%)"))
    total = _parse_money(_get("Total"))

    # Source quotes
    for label in ("Subtotal", "GST (9%)", "Total"):
        for l in lines:
            if l.startswith(f"{label}:"):
                source_quotes[label] = l.strip()

    return Invoice(
        doc_id=doc_id,
        invoice_number=number,
        invoice_date=inv_date,
        seller_name=seller,
        seller_uen=seller_uen,
        buyer_name=buyer,
        line_items=line_items,
        subtotal=subtotal,
        gst=gst,
        total=total,
        page=1,
        source_quotes=source_quotes,
    )


# ---------------------------------------------------------------------------
# Bank statement parser
# ---------------------------------------------------------------------------

def _parse_bank_statement(doc_id: str, pages: list[str]) -> BankStatement:
    text = pages[0] if pages else ""
    lines = [l for l in text.splitlines() if l.strip()]

    def _get(label: str) -> str:
        for l in lines:
            if l.startswith(f"{label}:"):
                return l.split(":", 1)[1].strip()
        return ""

    # Period: "YYYY-MM-DD to YYYY-MM-DD"
    period_raw = _get("Period")
    period_parts = period_raw.split(" to ")
    period_start = _parse_date(period_parts[0])
    period_end = _parse_date(period_parts[1])

    bank = _get("Bank")
    holder = _get("Account Holder")
    opening = _parse_money(_get("Opening Balance"))
    closing = _parse_money(_get("Closing Balance"))

    transactions: list[Transaction] = []
    source_quotes: dict[str, str] = {}
    in_table = False
    for l in lines:
        if l.startswith("Date | Description"):
            in_table = True
            continue
        if in_table:
            if l.startswith("Closing Balance:"):
                break
            parts = [p.strip() for p in l.split(" | ")]
            if len(parts) == 5:
                d_s, desc, debit_s, credit_s, bal_s = parts
                debit = _parse_money(debit_s) if debit_s != "-" else None
                credit = _parse_money(credit_s) if credit_s != "-" else None
                balance = _parse_money(bal_s)
                t = Transaction(
                    date=_parse_date(d_s),
                    description=desc,
                    debit=debit,
                    credit=credit,
                    balance=balance,
                )
                transactions.append(t)
                source_quotes[desc] = l.strip()

    return BankStatement(
        doc_id=doc_id,
        bank_name=bank,
        account_holder=holder,
        period_start=period_start,
        period_end=period_end,
        opening_balance=opening,
        closing_balance=closing,
        transactions=transactions,
        page=1,
        source_quotes=source_quotes,
    )


# ---------------------------------------------------------------------------
# Financial statements parser
# ---------------------------------------------------------------------------

def _parse_fs_summary(doc_id: str, pages: list[str]) -> FinancialStatementSummary:
    text = pages[0] if pages else ""
    lines = [l for l in text.splitlines() if l.strip()]

    def _get(label: str) -> str:
        for l in lines:
            if l.startswith(f"{label}:"):
                return l.split(":", 1)[1].strip()
        return ""

    return FinancialStatementSummary(
        doc_id=doc_id,
        company_name=_get("Company"),
        uen=_get("UEN"),
        financial_year_end=_parse_date(_get("Financial Year End")),
        revenue=_parse_money(_get("Revenue")),
        total_assets=_parse_money(_get("Total Assets")),
        total_liabilities=_parse_money(_get("Total Liabilities")),
        total_equity=_parse_money(_get("Total Equity")),
        page=1,
    )


# ---------------------------------------------------------------------------
# Entity self-cert AcroForm parser
# ---------------------------------------------------------------------------

def _parse_entity_self_cert(doc_id: str, path: Path) -> EntitySelfCert:
    """Parse a GLDB FATCA/CRS Entity AcroForm using pypdf get_fields()."""
    reader = pypdf.PdfReader(str(path))
    raw: dict[str, Any] = {}
    fields = reader.get_fields() or {}
    for name, field in fields.items():
        val = field.get("/V")
        if val is not None:
            raw[name] = str(val)

    def _text(key: str) -> str | None:
        v = raw.get(key, "").strip()
        return v if v else None

    def _checked(key: str) -> bool:
        return raw.get(key, "") == "/Yes"

    # Part 1 — identification
    legal_name = _text("Full Legal Name of Entity Branch")
    coi = _text("Country of Incorporation")
    reg_addr = Address(
        block=_text("Block"),
        street=_text("Street") or _text("Street_2") or _text("Street_3"),
        floor=_text("Floor"),
        unit=_text("Unit"),
        building=_text("Building") or _text("Building_2"),
        city_state=_text("City  State"),
        postal_code=_text("Postal Code"),
        country=_text("Country"),
    )
    mailing_different = _checked("Check Box1")
    mail_addr = Address(
        block=_text("Block_2"),
        street=_text("Street_4") or _text("Street_5") or _text("Street_6"),
        floor=_text("Floor_2"),
        unit=_text("Unit_2"),
        building=_text("Building_3") or _text("Building_4"),
        city_state=_text("City  State_2"),
        postal_code=_text("Postal Code_2"),
        country=_text("Country_2"),
    )
    mailing_explanation_key = (
        "If the country of the mailing address does not match the country of the "
        "registered address please explainRow1"
    )
    mailing_explanation = _text(mailing_explanation_key)

    # Part 2 — branch
    branch_ticked: list[str] = []
    if _checked("Check Box2"):
        branch_ticked.append("No")
    if _checked("Check Box3"):
        branch_ticked.append("Yes")
    hq_jurisdiction = _text("No YesIf yes in which jurisdiction is the headquarters located")

    # Part 3 — US person
    us_person_ticked: list[str] = []
    if _checked("Check Box4"):
        us_person_ticked.append("No")
    if _checked("Check Box5"):
        us_person_ticked.append("Yes")
    us_tin = _text("Text6")

    # Part 4 — tax residency rows
    expl_tmpl = "Explanation for Reason BCountry {n} indicated above because"
    tax_rows: list[TaxResidencyRow] = []
    row_map = [
        ("Country Jurisdiction of Tax Residence1", "Tax Identification Number TIN1",
         "Check Box7", "Check Box8", "Check Box9", expl_tmpl.format(n=1), 1),
        ("Country Jurisdiction of Tax Residence2", "Tax Identification Number TIN2",
         "Check Box10", "Check Box11", "Check Box12", expl_tmpl.format(n=2), 2),
        ("Country Jurisdiction of Tax Residence3", "Tax Identification Number TIN3",
         "Check Box13", "Check Box14", "Check Box15", expl_tmpl.format(n=3), 3),
    ]
    for country_key, tin_key, a_key, b_key, c_key, expl_key, row_num in row_map:
        country = _text(country_key)
        tin = _text(tin_key)
        reasons: list[str] = []
        if _checked(a_key):
            reasons.append("A")
        if _checked(b_key):
            reasons.append("B+")
        if _checked(c_key):
            reasons.append("C")
        expl = _text(expl_key)
        if country or tin or reasons or expl:
            tax_rows.append(TaxResidencyRow(
                row=row_num,
                country=country,
                tin=tin,
                reasons_ticked=reasons,
                explanation=expl,
            ))

    # Part 5 — entity type
    entity_type_keys = {
        "A": "Check Box16", "B": "Check Box17", "C": "Check Box18",
        "D": "Check Box24", "E": "Check Box25", "F": "Check Box26",
        "G": "Check Box27", "H": "Check Box28", "I": "Check Box29",
        "J": "Check Box30", "K": "Check Box31", "L": "Check Box32",
        "M": "Check Box33",
    }
    entity_types_ticked = [letter for letter, key in entity_type_keys.items() if _checked(key)]
    giin_parts = [_text("Text21"), _text("Text22"), _text("Text23")]

    # Part 6 — controlling persons
    cp_slots = [
        ("Text47", {
            "legal_ownership": "Check Box34",
            "legal_senior_managing_official": "Check Box36",
            "legal_other_means": "Check Box37",
            "trust_settlor": "Check Box38",
            "trust_trustee": "Check Box39",
            "trust_protector": "Check Box40",
            "trust_beneficiary": "Check Box41",
            "trust_other": "Check Box42",
            "arrangement_settlor_eq": "Check Box35",
            "arrangement_trustee_eq": "Check Box43",
            "arrangement_protector_eq": "Check Box44",
            "arrangement_beneficiary_eq": "Check Box45",
            "arrangement_other_eq": "Check Box46",
        }, 1),
        ("Text48", {
            "legal_ownership": "Check Box49",
            "legal_senior_managing_official": "Check Box50",
            "legal_other_means": "Check Box51",
            "trust_settlor": "Check Box52",
            "trust_trustee": "Check Box53",
            "trust_protector": "Check Box54",
            "trust_beneficiary": "Check Box55",
            "trust_other": "Check Box56",
            "arrangement_settlor_eq": "Check Box57",
            "arrangement_trustee_eq": "Check Box58",
            "arrangement_protector_eq": "Check Box59",
            "arrangement_beneficiary_eq": "Check Box60",
            "arrangement_other_eq": "Check Box61",
        }, 2),
        ("Text75", {
            "legal_ownership": "Check Box62",
            "legal_senior_managing_official": "Check Box63",
            "legal_other_means": "Check Box64",
            "trust_settlor": "Check Box65",
            "trust_trustee": "Check Box66",
            "trust_protector": "Check Box67",
            "trust_beneficiary": "Check Box68",
            "trust_other": "Check Box69",
            "arrangement_settlor_eq": "Check Box70",
            "arrangement_trustee_eq": "Check Box71",
            "arrangement_protector_eq": "Check Box72",
            "arrangement_beneficiary_eq": "Check Box73",
            "arrangement_other_eq": "Check Box74",
        }, 3),
    ]
    controlling_persons: list[ControllingPerson] = []
    for name_key, type_map, slot_num in cp_slots:
        name = _text(name_key)
        types = [label for label, key in type_map.items() if _checked(key)]
        if name or types:
            controlling_persons.append(ControllingPerson(
                slot=slot_num,
                name=name,
                types_ticked=types,
            ))

    # Part 7 — signatories
    sig_slots = [
        ("Name", "Designation", "Date DDMMYY", "Signature76", 1),
        ("Name_2", "Designation_2", "Date DDMMYY_2", "Signature77", 2),
    ]
    signatories: list[Signatory] = []
    for name_key, desig_key, date_key, sig_key, slot_num in sig_slots:
        name = _text(name_key)
        desig = _text(desig_key)
        date_raw = _text(date_key)
        sig_signed = _checked(sig_key)
        parsed_date: date | None = None
        if date_raw:
            parsed_date = _parse_ddmmyy(date_raw)
        if name or desig or date_raw:
            signatories.append(Signatory(
                slot=slot_num,
                name=name,
                designation=desig,
                date_raw=date_raw,
                date=parsed_date,
                signature_field_signed=sig_signed,
            ))

    # Determine template_id from metadata
    meta = reader.metadata or {}
    producer_raw = meta.get("/Producer")
    mod_raw = meta.get("/ModDate")
    template_id: str | None = None
    # The GLDB template has a known producer and the doc_id signals entity form
    if "fatca_crs_entity" in doc_id:
        template_id = "gldb_fatca_crs_entity_2025-11"

    return EntitySelfCert(
        doc_id=doc_id,
        template_id=template_id,
        legal_name=legal_name,
        country_of_incorporation=coi,
        registered_address=reg_addr,
        mailing_different=mailing_different,
        mailing_address=mail_addr,
        mailing_explanation=mailing_explanation,
        branch_ticked=branch_ticked,
        hq_jurisdiction=hq_jurisdiction,
        us_person_ticked=us_person_ticked,
        us_tin=us_tin,
        tax_rows=tax_rows,
        entity_types_ticked=entity_types_ticked,
        giin_parts=giin_parts,
        controlling_persons=controlling_persons,
        signatories=signatories,
        raw_fields=raw,
    )


def _parse_ddmmyy(s: str) -> date | None:
    """Parse DD/MM/YY or DD/MM/YYYY date strings. Returns None if unparseable."""
    s = s.strip()
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Pack parser — public entry point
# ---------------------------------------------------------------------------

def parse_pack(
    case_id: str,
    docs: list[DocMeta],
    pdf_paths: list[Path],
    manifest: ApplicationManifest | None,
) -> "ExtractedPack":
    """Parse all documents in a pack and return a populated ExtractedPack."""
    from gldb.schemas import ExtractedPack

    path_map: dict[str, Path] = {p.stem: p for p in pdf_paths}

    invoices = []
    bank_statements = []
    financial_statements = []
    entity_self_certs = []

    for doc in docs:
        p = path_map.get(doc.doc_id)
        if p is None:
            continue
        pages = read_text(p)

        if doc.doc_type == "invoice":
            invoices.append(_parse_invoice(doc.doc_id, pages))
        elif doc.doc_type == "bank_statement":
            bank_statements.append(_parse_bank_statement(doc.doc_id, pages))
        elif doc.doc_type == "financial_statements":
            financial_statements.append(_parse_fs_summary(doc.doc_id, pages))
        elif doc.doc_type == "fatca_crs_entity":
            entity_self_certs.append(_parse_entity_self_cert(doc.doc_id, p))

    return ExtractedPack(
        case_id=case_id,
        docs=docs,
        invoices=invoices,
        bank_statements=bank_statements,
        financial_statements=financial_statements,
        entity_self_certs=entity_self_certs,
        manifest=manifest,
        extraction_mode="stub",
    )
