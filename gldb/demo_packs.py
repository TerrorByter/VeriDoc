"""
gldb/demo_packs.py
------------------
Builds the demo packs in the format of the team's sample (four of the files
in data/templates/sample/): for each case, edited
copies of the sample's fillable bank statement, FATCA/CRS entity and
individual forms and financial workbook, plus application.json. The ACRA
profile and Notice of Assessment are not in a pack: the bank fetches them,
so each company's ACRA and IRAS records go into the mock service's data
(data/mock_gov) instead.

Every statement's sales and goods purchases also get an invoice, a purchase
order and a bill of lading (gldb.demo_trade) that agree with the payment to
the cent; a case may make one of them disagree.

Every company is the ACMA sample at another scale (every amount times one
factor, so the documents still agree with each other and with ACRA), and every
case changes it in exactly the ways its story needs. ACMA's own records are the
team's sample as issued (its ACRA profile lists the chairman and the CEO, and
each has a Notice of Assessment), so its clean case raises nothing.
ground_truth.json says what the pipeline should find; only the evaluation reads
it, never the pipeline.

Run:  python -m gldb.demo_packs
"""

from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

from gldb import demo_trade
from gldb.config import PACKS_DIR, money
from gldb.extract_stub import parse_bank_statement_form
from gldb.schemas import Transaction

SAMPLE_DIR = Path("data/templates/sample")
STATEMENT = SAMPLE_DIR / "ACMA BANK 1.pdf"
ENTITY_FORM = SAMPLE_DIR / "ACMA ENTITY.pdf"
INDIVIDUAL_FORM = SAMPLE_DIR / "ACMA INDIV.pdf"
WORKBOOK = SAMPLE_DIR / "Submitted financials.xlsx"
MOCK_GOV_DIR = Path("data/mock_gov")

#: The day each application is made, the day its forms are signed, and when the
#: bank made the statement (and, for the tampered one, when it was edited)
APPLICATION_DATE = date(2026, 7, 10)
SIGNED = "08/07/2026"
STATEMENT_MADE = "D:20260702091500+08'00'"
STATEMENT_EDITED = "D:20260723221400+08'00'"

#: The sample's ACRA financial block (SGD millions, as ACRA prints it)
ACMA_FILED = {
    "total_assets": "6.749", "total_current_assets": "6.166", "total_liabilities": "5.657",
    "total_current_liabilities": "5.416", "retained_earnings": "-194.497", "revenue": "7.935",
    "profit_before_tax": "-1.541", "profit_after_tax": "-1.548", "ebit": "-1.499",
    "operating_cashflow": "0.371", "investing_cashflow": "-0.122", "financing_cashflow": "-0.137",
}


@dataclass(frozen=True)
class Address:
    block: str
    street: str
    floor: str
    unit: str
    building: str
    postal: str

    def acra(self) -> str:
        """As ACRA prints a registered office."""
        return f"{self.block} {self.street} #{self.floor}-{self.unit} {self.building} ({self.postal})"

    def manifest(self) -> dict[str, str]:
        return {"block": self.block, "street": self.street, "floor": self.floor, "unit": self.unit,
                "building": self.building, "city_state": "SINGAPORE", "postal_code": self.postal,
                "country": "SINGAPORE"}


@dataclass(frozen=True)
class Person:
    name: str
    id_number: str
    position: str
    appointed: str
    born: str
    home: Address
    #: The Notice of Assessment IRAS holds: (income, deductions, tax payable); None if none
    tax: tuple[str, str, str] | None = ("131862.00", "22540.00", "6522.03")
    #: How IRAS addresses the person
    title: str = "MR"


@dataclass(frozen=True)
class Company:
    name: str
    uen: str
    incorporated: str
    office: Address
    chairman: Person
    ceo: Person
    shareholders: tuple[tuple[str, int], ...]
    #: Every amount is the sample's times this
    scale: Decimal
    account: str
    branch: str
    status: str = "LIVE COMPANY"
    #: How ACRA prints the registered office, if not as the forms give it
    acra_office: str | None = None


@dataclass(frozen=True)
class Case:
    """A demo pack: a company and what its documents do to it."""
    name: str
    company: Company
    note: str
    expected_band: str
    expected_path: str
    expected_readiness: str
    expected_check_ids: tuple[str, ...] = ()
    expected_compliance_check_ids: tuple[str, ...] = ()
    #: The workbook's figures are this times what ACRA holds
    inflate_financials: Decimal = Decimal(1)
    #: The statement stops at the end of this month (None: the full six months)
    statement_until: date | None = None
    #: A sales credit raised by this much without its balances, saved as an edit
    tamper_credit: Decimal | None = None
    #: Who signs the entity form, and the postal code it gives the office (None: the company's own)
    signatory: Person | None = None
    form_postal: str | None = None
    #: The name the individual form gives the chairman (None: their own)
    declared_name: str | None = None
    #: Guarantors besides the chairman, each with an individual form of their own
    extra_guarantors: tuple[Person, ...] = ()
    #: (partner on the statement, demo_trade fault): a trade whose documents disagree
    trade_faults: tuple[tuple[str, str], ...] = ()
    tampers: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def _home(block: str, street: str, floor: str, unit: str, postal: str) -> Address:
    return Address(block, street, floor, unit, "", postal)


ACMA = Company(
    name="ACMA LTD.", uen="196500233E", incorporated="1965-10-09",
    office=Address("67", "19 JURONG PORT ROAD", "25", "550", "JURONG TOWER 2", "619093"),
    chairman=Person("TAN BEE HUAT", "S2015888E", "CHAIRMAN", "2020-10-11", "20/05/1982",
                    _home("123", "HOUGANG AVENUE 3", "15", "243", "478236")),
    # As the team's ACRA profile, individual form and Notice of Assessment give him
    ceo=Person("MATTHEW TAN", "S8294716J", "CHIEF EXECUTIVE OFFICER", "2020-10-11", "14/03/1979",
               _home("412", "Jurong West Street 42", "09", "318", "650412"),
               tax=("119177.95", "16298.86", "5781.10")),
    shareholders=(("BOBBY DARYL LEE", 25000000),), scale=Decimal(1), account="111-234-567-890",
    branch="Tampines Branch",
    # The team's ACRA profile prints the street and postal code alone
    acra_office="19 JURONG PORT ROAD (619093)",
)


#: A guarantor IRAS has no Notice of Assessment for (the incomplete case)
UNASSESSED = Person("ONG KAH HENG", "S8011234D", "GUARANTOR", "2026-07-10", "02/09/1980",
                    _home("55", "TAMPINES STREET 21", "06", "214", "520055"), tax=None)


def _company(name: str, uen: str, incorporated: str, office: Address, chairman: tuple[str, str, Address, str],
             ceo: tuple[str, str], scale: str, account: str, branch: str, shareholder: str | None = None,
             status: str = "LIVE COMPANY") -> Company:
    chair = Person(chairman[0], chairman[1], "CHAIRMAN", incorporated, "03/09/1975", chairman[2], title=chairman[3])
    chief = Person(ceo[0], ceo[1], "CHIEF EXECUTIVE OFFICER", incorporated, "21/11/1983",
                   _home("2", "TAMPINES CENTRAL 7", "11", "08", "529602"), tax=None)
    return Company(name, uen, incorporated, office, chair, chief,
                   ((shareholder or chair.name, 1000000),), Decimal(scale), account, branch, status)


BRIGHTWAVE = _company(
    "BRIGHTWAVE COMPONENTS PTE. LTD.", "201733321K", "2017-11-02",
    Address("21", "UBI ROAD 1", "04", "12", "UBI TECHPARK", "408724"),
    ("LIM SOO KENG", "S6812345J", _home("456", "ANG MO KIO AVENUE 10", "09", "1180", "560456"), "MR"),
    ("PRIYA NAIR", "S8023456K"), "1.4", "222-345-678-901", "Ang Mo Kio Branch",
)
CORVID = _company(
    "CORVID MARINE ENGINEERING PTE. LTD.", "201422219M", "2014-08-19",
    Address("5", "TUAS SOUTH AVENUE 2", "02", "01", "TUAS MARINE HUB", "637503"),
    ("ONG HUI LING", "S7034567C", _home("88", "PASIR RIS DRIVE 4", "06", "330", "510088"), "MS"),
    ("ARJUN MEHTA", "S8545678D"), "0.8", "333-456-789-012", "Jurong East Branch",
)
DUSKFIELD = _company(
    "DUSKFIELD TRADING PTE. LTD.", "201955532N", "2019-05-27",
    Address("10", "ANSON ROAD", "15", "07", "INTERNATIONAL PLAZA", "079903"),
    ("KOH WEI JIE", "S7656789E", _home("301", "CLEMENTI AVENUE 2", "12", "45", "120301"), "MR"),
    ("SARAH LOW", "S8867890F"), "1.2", "444-567-890-123", "Raffles Place Branch",
)
EASTGATE = _company(
    "EASTGATE HOLDINGS PTE. LTD.", "201611144P", "2016-01-12",
    Address("1", "CHANGI BUSINESS PARK CRESCENT", "06", "02", "PLAZA 8", "486025"),
    ("GOH MEI XIN", "S7278901G", _home("17", "BEDOK NORTH ROAD", "03", "21", "460017"), "MS"),
    ("RAHUL BOSE", "S8689012H"), "0.9", "555-678-901-234", "Bedok Branch", shareholder="VIKTOR ALDANE",
)
FERNLEAF = _company(
    "FERNLEAF SUPPLIES PTE. LTD.", "201288855Q", "2012-03-30",
    Address("33", "WOODLANDS LOOP", "05", "18", "WOODLANDS CENTRE", "738330"),
    ("TEO KAI MING", "S6989012H", _home("620", "YISHUN RING ROAD", "10", "512", "760620"), "MR"),
    ("NUR AISYAH", "S9090123I"), "0.7", "666-789-012-345", "Woodlands Branch", status="STRUCK OFF",
)

OAKRIDGE = _company(
    "OAKRIDGE FABRICATION PTE. LTD.", "201833377R", "2018-04-17",
    Address("9", "SIN MING LANE", "03", "14", "SIN MING TECHHUB", "573970"),
    ("WONG MEI LING", "S7512345M", _home("210", "SERANGOON AVENUE 2", "08", "301", "550210"), "MS"),
    ("FAISAL HASSAN", "S8156789N"), "1.1", "777-890-123-456", "Serangoon Branch",
)

CASES: tuple[Case, ...] = (
    Case("clean", ACMA,
         "ACMA's documents agree with each other and with ACRA and IRAS: its chairman and CEO sign and guarantee, "
         "and both are ACRA officers with a notice at IRAS. Expect nothing to be raised.",
         "Low", "fast_track", "Complete", extra_guarantors=(ACMA.ceo,)),
    Case("incomplete", ACMA,
         "Three months of statements, and a third guarantor IRAS has no notice for. Expect an incomplete case.",
         "Low", "standard_review", "Incomplete",
         expected_compliance_check_ids=("BANK_COVERAGE", "CHECKLIST_MISSING_DOC"),
         statement_until=date(2026, 3, 31), extra_guarantors=(ACMA.ceo, UNASSESSED),
         tampers=({"doc": "bank_statement", "type": "months_missing", "check_ids": ["BANK_COVERAGE"],
                   "detail": "statement covers January to March only"},
                  {"doc": "pack", "type": "guarantor_without_notice", "check_ids": ["CHECKLIST_MISSING_DOC"],
                   "detail": "IRAS holds no notice for the third guarantor"})),
    Case("inflated_financials", BRIGHTWAVE,
         "The submitted financials double what Brightwave filed with ACRA; the bank shows the real revenue.",
         "High", "enhanced_review", "Complete", ("FS_ACRA_MISMATCH", "XDOC_BANK_REVENUE"),
         inflate_financials=Decimal(2),
         tampers=({"doc": "Submitted financials", "type": "figures_inflated",
                   "check_ids": ["FS_ACRA_MISMATCH", "XDOC_BANK_REVENUE"],
                   "detail": "every figure doubled against the ACRA filing"},)),
    Case("tampered_statement", CORVID,
         "A sales credit on Corvid's statement was raised and the file re-saved; its balances no longer add up, "
         "and the invoice for that sale no longer matches the credit.",
         "High", "enhanced_review", "Complete",
         ("BANK_RUNNING_BAL", "FORENSIC_INCREMENTAL_UPDATE", "META_MOD_AFTER_CREATE", "XDOC_INVOICE_BANK"),
         tamper_credit=Decimal("250000.00"),
         tampers=({"doc": "bank_statement", "type": "credit_raised",
                   "check_ids": ["BANK_RUNNING_BAL", "FORENSIC_INCREMENTAL_UPDATE", "META_MOD_AFTER_CREATE"],
                   "detail": "a sales credit raised by SGD 250,000 in an incremental save three weeks later"},
                  {"doc": "invoice_INV-1001", "type": "credit_not_invoiced", "check_ids": ["XDOC_INVOICE_BANK"],
                   "detail": "the invoice for that sale is for SGD 250,000 less than the credit now on the statement"})),
    Case("identity_mismatch", DUSKFIELD,
         "Duskfield's forms disagree with ACRA: a signatory who is no officer, another office, a guarantor's name.",
         "High", "enhanced_review", "Incomplete", ("XDOC_ACRA_ENTITY", "XDOC_ACRA_PERSON", "XDOC_ACRA_SIGNATORY"),
         expected_compliance_check_ids=("FORM_ENT_ADDRESS_MATCH", "FORM_ENT_SIGNATORY"),
         signatory=Person("DAVID CHUA", "S7911111A", "DIRECTOR", "2019-05-27", "01/01/1979",
                          _home("9", "SIMEI STREET 1", "04", "09", "529941")),
         form_postal="068811", declared_name="KOH WEI JUN",
         tampers=({"doc": "fatca_crs_entity", "type": "signatory_not_officer",
                   "check_ids": ["XDOC_ACRA_SIGNATORY", "FORM_ENT_SIGNATORY"],
                   "detail": "signed by DAVID CHUA, who is no officer at ACRA nor a signatory in the application"},
                  {"doc": "fatca_crs_entity", "type": "office_moved", "check_ids": ["XDOC_ACRA_ENTITY", "FORM_ENT_ADDRESS_MATCH"],
                   "detail": "registered office postal code 068811, ACRA has 079903"},
                  {"doc": "fatca_crs_individual", "type": "name_changed", "check_ids": ["XDOC_ACRA_PERSON"],
                   "detail": "the chairman's NRIC under the name KOH WEI JUN"})),
    Case("sanctioned", EASTGATE, "Eastgate's shareholder at ACRA is on the sanctions list.",
         "Medium", "enhanced_review", "Complete", ("SANCTIONS_MATCH",),
         tampers=({"doc": "pack", "type": "sanctioned_shareholder", "check_ids": ["SANCTIONS_MATCH"],
                   "detail": "shareholder VIKTOR ALDANE is on the sanctions list"},)),
    Case("struck_off", FERNLEAF, "Fernleaf applies for a loan, but ACRA has struck the company off.",
         "Medium", "standard_review", "Complete", ("REGISTRY_ENTITY_INACTIVE",),
         tampers=({"doc": "pack", "type": "struck_off", "check_ids": ["REGISTRY_ENTITY_INACTIVE"],
                   "detail": "ACRA status STRUCK OFF"},)),
    Case("invoice_mismatch", OAKRIDGE,
         "Oakridge's invoices disagree with its other paperwork: two charge more than they were ordered for and paid, "
         "one bills more goods than were shipped, one names an order that was never issued.",
         "High", "enhanced_review", "Complete", ("XDOC_INVOICE_BANK", "XDOC_PO_INVOICE", "XDOC_BL_INVOICE"),
         trade_faults=(("EFG Company", "overbilled"), ("Metallica Inc.", "short_shipped"),
                       ("SteelMen Pte Ltd.", "overbilled"), ("Caterpillarz Inc.", "no_order")),
         tampers=({"doc": "invoice_INV-1001", "type": "sale_overbilled", "check_ids": ["XDOC_PO_INVOICE", "XDOC_INVOICE_BANK"],
                   "detail": "EFG Company is billed 25% more than its order and than the credit received"},
                  {"doc": "invoice_INV-1002", "type": "goods_not_shipped", "check_ids": ["XDOC_BL_INVOICE"],
                   "detail": "Metallica Inc. is billed for 25 units; the bill of lading shows 18 shipped"},
                  {"doc": "invoice_SM-260128", "type": "purchase_overbilled", "check_ids": ["XDOC_PO_INVOICE", "XDOC_INVOICE_BANK"],
                   "detail": "SteelMen bills 25% more than the order and than the debit paid"},
                  {"doc": "invoice_CAT-260331", "type": "order_never_issued", "check_ids": ["XDOC_PO_INVOICE"],
                   "detail": "Caterpillarz names PO-4004, which is not in the pack"})),
)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def _write_form(template: Path, out: Path, values: dict[str, str], metadata: dict[str, str] | None = None) -> None:
    """A fresh copy of the fillable *template* with *values* in its fields,
    keeping the template's document information unless *metadata* says otherwise."""
    reader = PdfReader(str(template))
    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, values, auto_regenerate=False)
    writer.set_need_appearances_writer(True)
    writer.add_metadata({**{k: str(v) for k, v in (reader.metadata or {}).items()}, **(metadata or {})})
    with out.open("wb") as fh:
        writer.write(fh)


def _amount(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.2f}"


def _scaled_rows(company: Company, until: date | None) -> tuple[Decimal, list[Transaction]]:
    """The sample statement's opening balance and rows at the company's
    scale, balances recomputed so they add up exactly."""
    sample = parse_bank_statement_form("sample", STATEMENT)
    balance = money(sample.opening_balance * company.scale)
    opening, rows = balance, []
    for t in sample.transactions:
        if until is not None and t.date > until:
            break
        credit = money(t.credit * company.scale) if t.credit is not None else None
        debit = money(t.debit * company.scale) if t.debit is not None else None
        balance = balance + (credit or 0) - (debit or 0)
        rows.append(Transaction(date=t.date, description=t.description, credit=credit, debit=debit, balance=balance))
    return opening, rows


def seller_of(company: Company) -> demo_trade.Seller:
    """*company* as its invoices print it."""
    office = company.office
    street = f"{office.street.title()}, #{office.floor}-{office.unit}" + (f" {office.building.title()}" if office.building else "")
    return demo_trade.Seller(company.name, company.uen, (street, f"Singapore {office.postal}"),
                             f"{demo_trade.BANK}, {company.branch}", company.account)


def _statement(case: Case, out: Path) -> None:
    company = case.company
    opening, rows = _scaled_rows(company, case.statement_until)
    end = case.statement_until or date(2026, 6, 30)
    values = {
        "F01005": company.account, "F01006": end.strftime("%d/%m/%Y"),
        "F01007": f"01/01/2026 to {end:%d/%m/%Y}", "F01008": company.name,
        "F01009": f"{company.office.block} {company.office.street.title()}, #{company.office.floor}-{company.office.unit}",
        "F01010": f"Singapore {company.office.postal}", "F01011": company.branch,
        "F01012": _amount(opening), "F01013": _amount(sum((t.credit or Decimal(0) for t in rows), Decimal(0))),
        "F01014": _amount(sum((t.debit or Decimal(0) for t in rows), Decimal(0))),
        "F01015": _amount(rows[-1].balance), "F01017": str(len(rows)),
    }
    slots = len({n[:6] for n in PdfReader(str(STATEMENT)).get_fields() or {} if n.startswith("F02")})
    for n in range(1, slots + 1):
        t = rows[n - 1] if n <= len(rows) else None
        values.update({
            f"F02{n:03d}a": t.date.strftime("%d/%m/%Y") if t else "", f"F02{n:03d}b": t.description if t else "",
            f"F02{n:03d}c": _amount(t.credit) if t else "", f"F02{n:03d}d": _amount(t.debit) if t else "",
            f"F02{n:03d}e": _amount(t.balance) if t else "",
        })
    _write_form(STATEMENT, out, values, {"/CreationDate": STATEMENT_MADE, "/ModDate": STATEMENT_MADE})
    if case.tamper_credit is not None:
        _tamper_credit(out, rows, case.tamper_credit)


def _tamper_credit(path: Path, rows: list[Transaction], by: Decimal) -> None:
    """Raise the first sales credit by *by*, leaving the balances, in an
    incremental save three weeks after the statement was made: what editing
    a statement in a PDF editor leaves behind."""
    n, credit = next((i, c) for i, t in enumerate(rows, start=1)
                     if (c := t.credit) is not None and "Sales" in t.description)
    writer = PdfWriter(str(path), incremental=True)
    for page in writer.pages:
        writer.update_page_form_field_values(page, {f"F02{n:03d}c": _amount(credit + by)}, auto_regenerate=False)
    writer.add_metadata({"/ModDate": STATEMENT_EDITED, "/Producer": "Adobe Acrobat Pro (64-bit) 25.1.20"})
    edited = io.BytesIO()
    writer.write(edited)  # the original bytes, then the edit appended as a new revision
    path.write_bytes(edited.getvalue())


def _entity_form(case: Case, out: Path) -> None:
    company, office = case.company, case.company.office
    first = case.signatory or company.ceo
    _write_form(ENTITY_FORM, out, {
        "Full Legal Name of Entity Branch": company.name, "Block": office.block, "Street": office.street,
        "Floor": office.floor, "Unit": office.unit, "Building": office.building,
        "Postal Code": case.form_postal or office.postal, "Tax Identification Number TIN1": company.uen,
        "Name": first.name, "Designation": first.position, "Name_2": company.chairman.name,
        "Designation_2": company.chairman.position, "Date DDMMYY": SIGNED, "Date DDMMYY_2": SIGNED,
    })


def _guarantors(case: Case) -> list[Person]:
    return [case.company.chairman, *case.extra_guarantors]


def _individual_form(case: Case, person: Person, out: Path) -> None:
    """*person*'s individual form; the chairman's gives the name the case declares for them, if any."""
    home = person.home
    name = (case.declared_name if person == case.company.chairman else None) or person.name
    _write_form(INDIVIDUAL_FORM, out, {
        "Full Legal Name as per NRICPassport": name, "Date of Birth DDMMYYYY": person.born,
        "Block": home.block, "Street": home.street, "Floor": home.floor, "Unit": home.unit,
        "Postal Code": home.postal, "Name as per NRIC Passport": name, "NRIC Passport No": person.id_number,
        "Text90": person.id_number, "Date DDMMYY": SIGNED,
    })


_NUMBER_CELL = re.compile(r'(<c r="[A-Z]+\d+"(?: s="\d+")?>)<v>([^<]*)</v>')


def _workbook(case: Case, out: Path) -> None:
    """The sample workbook with every figure (all SGD millions) times the
    company's scale and the case's inflation."""
    factor = case.company.scale * case.inflate_financials

    def scaled(match: re.Match[str]) -> str:
        value = Decimal(repr(float(match[2]))) * factor
        return f"{match[1]}<v>{value.quantize(Decimal('0.000001')).normalize():f}</v>"

    with zipfile.ZipFile(WORKBOOK) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item)
            if item.filename.startswith("xl/worksheets/sheet"):
                data = _NUMBER_CELL.sub(scaled, data.decode("utf-8")).encode("utf-8")
            target.writestr(item, data)


# ---------------------------------------------------------------------------
# Records: the application, and what ACRA and IRAS hold
# ---------------------------------------------------------------------------


def _manifest(case: Case) -> dict[str, Any]:
    company = case.company
    guarantors = _guarantors(case)
    return {
        "application_id": f"APP-2026-{case.name.upper()}", "application_date": APPLICATION_DATE.isoformat(),
        "company_name": company.name, "uen": company.uen, "country_of_incorporation": "SINGAPORE",
        "date_of_incorporation": company.incorporated, "registered_address": company.office.manifest(),
        "authorised_signatories": [company.ceo.name, company.chairman.name],
        "guarantors": [{"name": g.name, "id_number": g.id_number, "nationality": "SINGAPORE",
                        "residential_address": g.home.manifest()} for g in guarantors],
    }


def acra_record(company: Company) -> dict[str, Any]:
    """ACRA's record of *company*: the sample's filing at its scale."""
    figures = {k: f"{(Decimal(v) * company.scale).quantize(Decimal('0.001'))}" for k, v in ACMA_FILED.items()}
    return {
        "company_name": company.name, "uen": company.uen, "incorporation_date": company.incorporated,
        "status": company.status, "registered_address": company.acra_office or company.office.acra(),
        "issued_on": demo_trade.RECORDS_ISSUED_ON,
        "officers": [{"name": p.name, "id_number": p.id_number, "position": p.position, "appointed": p.appointed,
                      "postal_code": p.home.postal}
                     for p in (company.chairman, company.ceo)],
        "shareholders": [{"name": n, "shares": s} for n, s in company.shareholders],
        "financials": {"period_start": "2025-01-01", "period_end": "2025-12-31", "currency": "SGD", **figures},
    }


def iras_records(company: Company) -> list[dict[str, Any]]:
    """IRAS's notices for the company's people: those who have one."""
    return [
        {"name": f"{p.title} {p.name}", "id_number": p.id_number, "year_of_assessment": 2025,
         "income": p.tax[0], "deductions": p.tax[1],
         "chargeable_income": f"{Decimal(p.tax[0]) - Decimal(p.tax[1]):.2f}", "tax_payable": p.tax[2],
         "postal_code": p.home.postal}
        for p in (company.chairman, company.ceo) if p.tax is not None
    ]


def _upsert(path: Path, key: str, ident: str, records: list[dict[str, Any]]) -> None:
    """Replace the records in *path*'s *key* list whose *ident* matches, keeping all others."""
    data = json.loads(path.read_text(encoding="utf-8"))
    mine = {r[ident] for r in records}
    data[key] = [r for r in data[key] if r[ident] not in mine] + records
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _truth(case: Case) -> dict[str, Any]:
    return {
        "pack": case.name, "company": case.company.name, "note": case.note,
        "expected_band": case.expected_band, "expected_path": case.expected_path,
        "expected_readiness": case.expected_readiness, "expected_check_ids": list(case.expected_check_ids),
        "expected_check_ids_ocr": [], "expected_compliance_check_ids": list(case.expected_compliance_check_ids),
        "expected_leads": [], "expected_triage": [], "tampers": list(case.tampers),
    }


def build_pack(case: Case, folder: Path) -> None:
    """Write *case*'s documents, application and ground truth into *folder*."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    _statement(case, folder / "bank_statement.pdf")
    _entity_form(case, folder / "fatca_crs_entity.pdf")
    for n, person in enumerate(_guarantors(case), start=1):
        _individual_form(case, person, folder / f"fatca_crs_individual{'' if n == 1 else f'_{n}'}.pdf")
    _workbook(case, folder / "Submitted financials.xlsx")
    for trade in demo_trade.plan(seller_of(case.company), _scaled_rows(case.company, case.statement_until)[1], case.trade_faults):
        demo_trade.write(folder, trade)
    (folder / "application.json").write_text(json.dumps(_manifest(case), indent=2) + "\n", encoding="utf-8")
    (folder / "ground_truth.json").write_text(json.dumps(_truth(case), indent=2) + "\n", encoding="utf-8")


def write_records(mock_dir: Path = MOCK_GOV_DIR) -> None:
    """Put every demo company's, and every supplier's, ACRA records and every demo company's
    IRAS records into the mock service's data."""
    companies = list({c.company.uen: c.company for c in CASES}.values())
    _upsert(mock_dir / "acra.json", "companies", "uen",
            [*(acra_record(c) for c in companies), *(demo_trade.acra_record(s) for s in demo_trade.suppliers())])
    _upsert(mock_dir / "iras.json", "notices", "id_number", [n for c in companies for n in iras_records(c)])


def generate(packs_dir: str | Path = PACKS_DIR, mock_dir: Path = MOCK_GOV_DIR) -> None:
    """Rebuild every demo pack and the demo companies' official records."""
    for case in CASES:
        build_pack(case, Path(packs_dir) / case.name)
    write_records(mock_dir)


if __name__ == "__main__":
    generate()
    print(f"Wrote {len(CASES)} demo packs to {PACKS_DIR}/ and their records to {MOCK_GOV_DIR}/")
