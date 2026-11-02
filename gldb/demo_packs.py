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
order and a bill of lading (an export) or delivery order (a purchase in
Singapore) from gldb.demo_trade that agree with the payment to the cent; a
case may make one of them disagree.

Every company is the ACMA sample at another scale (every amount times one
factor, so the documents still agree with each other and with ACRA), and every
case changes it in exactly the ways its story needs. Each company otherwise
has its own: customers and suppliers (gldb.demo_partners) where the sample's
statement names ACMA's, numbering, payment dates (a few days earlier in each
month), people, and entity type on the FATCA/CRS form. ACMA's own records are the
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
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

from gldb import demo_partners, demo_trade
from gldb.config import PACKS_DIR, money
from gldb.extract_stub import parse_bank_statement_form
from gldb.form_maps import ENTITY_FIELD_MAP
from gldb.schemas import Transaction

SAMPLE_DIR = Path("data/templates/sample")
STATEMENT = SAMPLE_DIR / "ACMA BANK 1.pdf"
ENTITY_FORM = SAMPLE_DIR / "ACMA ENTITY.pdf"
INDIVIDUAL_FORM = SAMPLE_DIR / "ACMA INDIV.pdf"
WORKBOOK = SAMPLE_DIR / "Submitted financials.xlsx"
MOCK_GOV_DIR = Path("data/mock_gov")
#: The entity form's checkbox for each entity type (Part 5), A to M
ENTITY_TYPE_BOX: dict[str, str] = ENTITY_FIELD_MAP["entity_type"]

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
    #: Who it trades with, in the order the sample statement's trades come (gldb.demo_partners)
    partners: tuple[demo_trade.Partner, ...]
    #: Its sales invoices are <invoice_prefix>-<numbers_from + n> (see demo_trade.Seller)
    invoice_prefix: str
    numbers_from: int
    #: Each statement row is this many days earlier than the sample's, but in the same month
    days_earlier: int = 0
    status: str = "LIVE COMPANY"
    #: How ACRA prints the registered office, if not as the forms give it
    acra_office: str | None = None
    #: Listed on an exchange (entity type K on the FATCA/CRS form), else an active business (G)
    listed: bool = False


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
    #: The statement describes each sales receipt as the bank's payment reference,
    #: "FAST CR <payer> <invoice number>", not as "Sales Revenue - <buyer>"
    receipts_name_payers: bool = False
    #: (customer, payer): that customer's sale is paid by someone else (receipts_name_payers)
    paid_by: tuple[tuple[str, str], ...] = ()
    #: Customers whose sale is paid in two parts (receipts_name_payers)
    paid_in_parts: tuple[str, ...] = ()
    #: A supplier whose payment comes back from it as a "customer receipt" (ROUND_TRIP_*)
    round_trip: str | None = None
    #: What the AI team should raise and how it should judge the rule findings (agent_eval);
    #: the rules never read them
    expected_leads: tuple[dict[str, str], ...] = ()
    expected_triage: tuple[dict[str, str], ...] = ()
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
    branch="Tampines Branch", partners=demo_partners.ACMA, invoice_prefix="INV", numbers_from=1000,
    # The team's ACRA profile prints the street and postal code alone
    acra_office="19 JURONG PORT ROAD (619093)",
    # ACMA LTD. files its financials with SGX, as the team's sample workbook shows
    listed=True,
)


#: A guarantor IRAS has no Notice of Assessment for (the incomplete case)
UNASSESSED = Person("ONG KAH HENG", "S8011234D", "GUARANTOR", "2026-07-10", "02/09/1980",
                    _home("55", "TAMPINES STREET 21", "06", "214", "520055"), tax=None)


def _company(name: str, uen: str, incorporated: str, office: Address, chairman: tuple[str, str, str, Address, str],
             ceo: tuple[str, str, str, Address], scale: str, account: str, branch: str,
             trade: tuple[tuple[demo_trade.Partner, ...], str, int, int], shareholder: str | None = None,
             status: str = "LIVE COMPANY") -> Company:
    """A company whose chairman is (name, NRIC, born, home, title), whose CEO is (name, NRIC, born, home) and
    whose *trade* is (partners, invoice prefix, numbers from, days earlier)."""
    chair = Person(chairman[0], chairman[1], "CHAIRMAN", incorporated, chairman[2], chairman[3], title=chairman[4])
    chief = Person(ceo[0], ceo[1], "CHIEF EXECUTIVE OFFICER", incorporated, ceo[2], ceo[3], tax=None)
    partners, prefix, numbers_from, days_earlier = trade
    return Company(name, uen, incorporated, office, chair, chief, ((shareholder or chair.name, 1000000),),
                   Decimal(scale), account, branch, partners, prefix, numbers_from, days_earlier, status)


BRIGHTWAVE = _company(
    "BRIGHTWAVE COMPONENTS PTE. LTD.", "201733321K", "2017-11-02",
    Address("21", "UBI ROAD 1", "04", "12", "UBI TECHPARK", "408724"),
    ("LIM SOO KENG", "S6812345J", "03/09/1968", _home("456", "ANG MO KIO AVENUE 10", "09", "1180", "560456"), "MR"),
    ("PRIYA NAIR", "S8023456K", "21/11/1980", _home("2", "TAMPINES CENTRAL 7", "11", "08", "529602")),
    "1.4", "222-345-678-901", "Ang Mo Kio Branch", (demo_partners.BRIGHTWAVE, "BW", 2600, 3),
)
CORVID = _company(
    "CORVID MARINE ENGINEERING PTE. LTD.", "201422219M", "2014-08-19",
    Address("5", "TUAS SOUTH AVENUE 2", "02", "01", "TUAS MARINE HUB", "637503"),
    ("ONG HUI LING", "S7034567C", "17/04/1970", _home("88", "PASIR RIS DRIVE 4", "06", "330", "510088"), "MS"),
    ("ARJUN MEHTA", "S8545678D", "08/06/1985", _home("108", "JURONG EAST STREET 13", "07", "214", "600108")),
    "0.8", "333-456-789-012", "Jurong East Branch", (demo_partners.CORVID, "CME", 4100, 1),
)
DUSKFIELD = _company(
    "DUSKFIELD TRADING PTE. LTD.", "201955532N", "2019-05-27",
    Address("10", "ANSON ROAD", "15", "07", "INTERNATIONAL PLAZA", "079903"),
    ("KOH WEI JIE", "S7656789E", "29/12/1976", _home("301", "CLEMENTI AVENUE 2", "12", "45", "120301"), "MR"),
    ("SARAH LOW", "S8867890F", "15/02/1988", _home("9", "TELOK BLANGAH CRESCENT", "18", "62", "090009")),
    "1.2", "444-567-890-123", "Raffles Place Branch", (demo_partners.DUSKFIELD, "DFT", 3300, 5),
)
EASTGATE = _company(
    "EASTGATE HOLDINGS PTE. LTD.", "201611144P", "2016-01-12",
    Address("1", "CHANGI BUSINESS PARK CRESCENT", "06", "02", "PLAZA 8", "486025"),
    ("GOH MEI XIN", "S7278901G", "05/07/1972", _home("17", "BEDOK NORTH ROAD", "03", "21", "460017"), "MS"),
    ("RAHUL BOSE", "S8689012H", "30/10/1986", _home("27", "SIMEI STREET 4", "09", "11", "529871")),
    "0.9", "555-678-901-234", "Bedok Branch", (demo_partners.EASTGATE, "EGH", 5800, 2), shareholder="VIKTOR ALDANE",
)
FERNLEAF = _company(
    "FERNLEAF SUPPLIES PTE. LTD.", "201288855Q", "2012-03-30",
    Address("33", "WOODLANDS LOOP", "05", "18", "WOODLANDS CENTRE", "738330"),
    ("TEO KAI MING", "S6989012H", "11/01/1969", _home("620", "YISHUN RING ROAD", "10", "512", "760620"), "MR"),
    ("NUR AISYAH", "S9090123I", "24/08/1990", _home("681", "WOODLANDS DRIVE 62", "04", "377", "730681")),
    "0.7", "666-789-012-345", "Woodlands Branch", (demo_partners.FERNLEAF, "FLS", 1400, 4), status="STRUCK OFF",
)

OAKRIDGE = _company(
    "OAKRIDGE FABRICATION PTE. LTD.", "201833377R", "2018-04-17",
    Address("9", "SIN MING LANE", "03", "14", "SIN MING TECHHUB", "573970"),
    ("WONG MEI LING", "S7512345M", "19/03/1975", _home("210", "SERANGOON AVENUE 2", "08", "301", "550210"), "MS"),
    ("FAISAL HASSAN", "S8156789N", "02/05/1981", _home("455", "SEMBAWANG DRIVE", "06", "123", "750455")),
    "1.1", "777-890-123-456", "Serangoon Branch", (demo_partners.OAKRIDGE, "OKF", 6200, 6),
)
KESTREL = _company(
    "KESTREL AUTOMATION PTE. LTD.", "201526618C", "2015-09-14",
    Address("2", "JALAN KILANG BARAT", "07", "03", "KILANG BARAT CENTRE", "159346"),
    ("HO JUN WEI", "S7322841B", "12/06/1973", _home("520", "BUKIT BATOK STREET 52", "07", "88", "650520"), "MR"),
    ("SITI RAHMAH", "S8433902C", "27/09/1984", _home("33", "PUNGGOL FIELD WALK", "12", "40", "828733")),
    "1.3", "888-901-234-567", "Bukit Merah Branch", (demo_partners.KESTREL, "KAU", 2200, 2),
)
LUMEN = _company(
    "LUMEN AQUA SYSTEMS PTE. LTD.", "202011479D", "2020-03-09",
    Address("7", "TAI SENG DRIVE", "04", "06", "TAI SENG CENTRE", "535218"),
    ("CHUA MING HAN", "S6944517E", "04/02/1969", _home("12", "BISHAN STREET 13", "14", "22", "570012"), "MR"),
    (*demo_partners.LUMEN_CEO, "18/10/1980", _home("158", "TOA PAYOH LORONG 1", "09", "402", "310158")),
    "0.6", "999-012-345-678", "Toa Payoh Branch", (demo_partners.LUMEN, "LAS", 4700, 4),
)
NORTHSTAR = _company(
    "NORTHSTAR COLD CHAIN PTE. LTD.", "201708814F", "2017-02-20",
    Address("18", "SENOKO DRIVE", "02", "11", "SENOKO INDUSTRIAL HUB", "758207"),
    ("YEO KAH LENG", "S6655120G", "09/08/1966", _home("701", "YISHUN AVENUE 5", "05", "215", "760701"), "MS"),
    ("DANIEL OOI", "S8266031H", "22/01/1982", _home("246", "COMPASSVALE STREET", "16", "35", "540246")),
    "0.95", "121-345-678-901", "Sembawang Branch", (demo_partners.NORTHSTAR, "NCC", 6800, 1),
)

#: How a bank describes a receipt by its payment reference (Case.receipts_name_payers)
RECEIPT_CHANNEL = "FAST"
#: A sale paid in parts: the first part is this share, the second follows this many days later
FIRST_PART_SHARE = Decimal("0.6")
SECOND_PART_AFTER_DAYS = 8
#: A round trip: the money comes back this many days later, less this share kept back
ROUND_TRIP_AFTER_DAYS = 13
ROUND_TRIP_SHARE = Decimal("0.975")
ROUND_TRIP_RECEIPT = "Customer Receipt"

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
                  {"doc": "invoice_CME-4101", "type": "credit_not_invoiced", "check_ids": ["XDOC_INVOICE_BANK"],
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
         trade_faults=(("Gebeng Steelworks Sdn. Bhd.", "overbilled"), ("Melaka Struktur Sdn. Bhd.", "short_shipped"),
                       ("Jurong Steel Stockists Pte. Ltd.", "overbilled"), ("Tuas Lifting Equipment Pte. Ltd.", "no_order")),
         tampers=({"doc": "invoice_OKF-6201", "type": "sale_overbilled", "check_ids": ["XDOC_PO_INVOICE", "XDOC_INVOICE_BANK"],
                   "detail": "Gebeng Steelworks is billed 25% more than its order and than the credit received"},
                  {"doc": "invoice_OKF-6202", "type": "goods_not_shipped", "check_ids": ["XDOC_BL_INVOICE"],
                   "detail": "Melaka Struktur is billed for 40 columns; the bill of lading shows 30 shipped"},
                  {"doc": "invoice_JSS-260122", "type": "purchase_overbilled", "check_ids": ["XDOC_PO_INVOICE", "XDOC_INVOICE_BANK"],
                   "detail": "Jurong Steel Stockists bills 25% more than the order and than the debit paid"},
                  {"doc": "invoice_TLE-260325", "type": "order_never_issued", "check_ids": ["XDOC_PO_INVOICE"],
                   "detail": "Tuas Lifting Equipment names PO-9204, which is not in the pack"})),
    # Beyond the rules: each paperwork set agrees to the cent, so the rules pass the case; only the AI team,
    # reading who paid, who runs the counterparties and where money goes, can find what is wrong
    Case("third_party_payment", KESTREL,
         "Kestrel's sale to Kulim Hi-Tech Packaging was paid by Golden Reef Trading Limited, a company with no part "
         "in it. Amount and date agree, so the rules pass it; only the AI team asks who paid.",
         "Low", "fast_track", "Complete", receipts_name_payers=True,
         paid_by=(("Kulim Hi-Tech Packaging Sdn. Bhd.", "GOLDEN REEF TRADING LIMITED"),),
         expected_leads=({"agent": "transaction", "pattern": "third_party_payment"},)),
    Case("related_party", LUMEN,
         "Lumen's biggest supplier, Harbour Ridge Filtration, is run by Lumen's own chief executive: ACRA lists her "
         "as its director. No document says so and no rule compares the two; only the AI team looks it up.",
         "Low", "fast_track", "Complete",
         expected_leads=({"agent": "kyc", "pattern": "related_party"},)),
    Case("round_trip", NORTHSTAR,
         "Northstar pays Kranji Refrigeration for a machine, and 13 days later nearly all of it comes "
         "back from Kranji as a customer receipt. Each row looks normal alone; only the AI team connects them.",
         "Low", "fast_track", "Complete", round_trip="Kranji Refrigeration Pte. Ltd.",
         expected_leads=({"agent": "transaction", "pattern": "round_trip"},)),
    Case("split_payment", KESTREL,
         "Kestrel's buyer Pelabuhan Logistik paid its invoice in two parts. The rules look for one matching credit "
         "and flag it; the AI team should find both parts came from the buyer, and code should explain the flag.",
         "Medium", "standard_review", "Complete", ("XDOC_INVOICE_BANK",), receipts_name_payers=True,
         paid_in_parts=("Pelabuhan Logistik Sdn. Bhd.",),
         expected_leads=({"agent": "transaction", "pattern": "split_payment"},),
         expected_triage=({"check_id": "XDOC_INVOICE_BANK", "status": "refuted"},)),
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
    scale and dates, each trade with the company's own partner, balances
    recomputed so they add up exactly."""
    sample = parse_bank_statement_form("sample", STATEMENT)
    own = {sample_partner.described: partner.described
           for sample_partner, partner in zip(demo_partners.ACMA, company.partners, strict=True)}
    balance = money(sample.opening_balance * company.scale)
    opening, rows = balance, []
    for t in sample.transactions:
        day = max(t.date - timedelta(days=company.days_earlier), t.date.replace(day=1))
        if until is not None and day > until:
            break
        credit = money(t.credit * company.scale) if t.credit is not None else None
        debit = money(t.debit * company.scale) if t.debit is not None else None
        balance = balance + (credit or 0) - (debit or 0)
        rows.append(Transaction(date=day, description=own.get(t.description, t.description), credit=credit, debit=debit,
                                balance=balance))
    return opening, rows


def seller_of(company: Company) -> demo_trade.Seller:
    """*company* as its invoices print it."""
    office = company.office
    street = (f"{office.block} {office.street.title()}, #{office.floor}-{office.unit}"
              + (f" {office.building.title()}" if office.building else ""))
    return demo_trade.Seller(company.name, company.uen, (street, f"Singapore {office.postal}"),
                             f"{demo_trade.BANK}, {company.branch}", company.account, company.invoice_prefix,
                             company.numbers_from)


def _balanced(opening: Decimal, rows: list[Transaction]) -> list[Transaction]:
    """*rows* in date order (same-day rows as they come), each balance recomputed from *opening*."""
    balance, out = opening, []
    for t in sorted(rows, key=lambda t: t.date):
        balance = balance + (t.credit or 0) - (t.debit or 0)
        out.append(t.model_copy(update={"balance": balance}))
    return out


def _partner(case: Case, name: str) -> demo_trade.Partner:
    found = next((p for p in case.company.partners if p.name == name), None)
    if found is None:
        raise ValueError(f"{case.name}: {name!r} is not one of {case.company.name}'s partners")
    return found


def _trade_rows(case: Case) -> tuple[Decimal, list[Transaction]]:
    """The statement's rows the trades are read from: the company's own, with
    the round trip's receipt added (it is no trade, so it has no paperwork)."""
    opening, rows = _scaled_rows(case.company, case.statement_until)
    if case.round_trip is not None:
        supplier = _partner(case, case.round_trip)
        paid = next(t for t in rows if t.description == supplier.described and t.debit is not None)
        assert paid.debit is not None
        rows = [*rows, Transaction(date=paid.date + timedelta(days=ROUND_TRIP_AFTER_DAYS),
                                   description=f"{ROUND_TRIP_RECEIPT} - {supplier.name}",
                                   credit=money(paid.debit * ROUND_TRIP_SHARE), debit=None, balance=Decimal(0))]
    return opening, _balanced(opening, rows)


def trades(case: Case) -> list[demo_trade.Trade]:
    """The paperwork of every sale and goods purchase on *case*'s statement."""
    company = case.company
    return demo_trade.plan(seller_of(company), _trade_rows(case)[1], company.partners, case.trade_faults)


def _receipts(case: Case, row: Transaction, trade: demo_trade.Trade) -> list[Transaction]:
    """A sale's receipt as the bank describes it by payment reference: who
    paid and the invoice, in two parts when the case says so."""
    assert row.credit is not None
    payer = dict(case.paid_by).get(trade.partner.name, trade.partner.legal_name)
    described = f"{RECEIPT_CHANNEL} CR {payer} {trade.invoice.number}"
    if trade.partner.name not in case.paid_in_parts:
        return [row.model_copy(update={"description": described})]
    first = money(row.credit * FIRST_PART_SHARE)
    return [row.model_copy(update={"description": f"{described} PART 1", "credit": first}),
            row.model_copy(update={"description": f"{described} PART 2", "credit": row.credit - first,
                                   "date": row.date + timedelta(days=SECOND_PART_AFTER_DAYS)})]


def statement_rows(case: Case) -> tuple[Decimal, list[Transaction]]:
    """The opening balance and the rows *case*'s statement prints."""
    opening, rows = _trade_rows(case)
    if not case.receipts_name_payers:
        if case.paid_by or case.paid_in_parts:
            raise ValueError(f"{case.name}: who paid shows only when receipts name their payers")
        return opening, rows
    for name in (*(c for c, _ in case.paid_by), *case.paid_in_parts):
        _partner(case, name)
    sales = iter(t for t in trades(case) if not t.purchase)
    sale_rows = {p.described for p in case.company.partners if not p.supplier}
    printed = [r for row in rows for r in (_receipts(case, row, next(sales)) if row.description in sale_rows and
                                            row.credit is not None else [row])]
    return opening, _balanced(opening, printed)


def _statement(case: Case, out: Path) -> None:
    company = case.company
    opening, rows = statement_rows(case)
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
    if len(rows) > slots:
        raise ValueError(f"{case.name}: {len(rows)} rows do not fit the statement's {slots}")
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
        # Part 5, the entity type: K a listed company, G an active business (the sample ticks K)
        ENTITY_TYPE_BOX["K"]: "/Yes" if company.listed else "/Off",
        ENTITY_TYPE_BOX["G"]: "/Off" if company.listed else "/Yes",
    })


def _guarantors(case: Case) -> list[Person]:
    return [case.company.chairman, *case.extra_guarantors]


def _declarants(case: Case) -> list[Person]:
    """Who files an individual form: each signatory (the chairman and the CEO) and each guarantor, once."""
    return list(dict.fromkeys([case.company.chairman, case.company.ceo, *case.extra_guarantors]))


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
        "expected_leads": list(case.expected_leads), "expected_triage": list(case.expected_triage),
        "tampers": list(case.tampers),
    }


def build_pack(case: Case, folder: Path) -> None:
    """Write *case*'s documents, application and ground truth into *folder*."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    _statement(case, folder / "bank_statement.pdf")
    _entity_form(case, folder / "fatca_crs_entity.pdf")
    for n, person in enumerate(_declarants(case), start=1):
        _individual_form(case, person, folder / f"fatca_crs_individual{'' if n == 1 else f'_{n}'}.pdf")
    _workbook(case, folder / "Submitted financials.xlsx")
    for trade in trades(case):
        demo_trade.write(folder, trade)
    (folder / "application.json").write_text(json.dumps(_manifest(case), indent=2) + "\n", encoding="utf-8")
    (folder / "ground_truth.json").write_text(json.dumps(_truth(case), indent=2) + "\n", encoding="utf-8")


def companies() -> list[Company]:
    return list({c.company.uen: c.company for c in CASES}.values())


def suppliers() -> list[demo_trade.Partner]:
    """Every demo company's suppliers: the companies registered at ACRA that its documents name."""
    return [partner for company in companies() for partner in company.partners if partner.supplier]


def write_records(mock_dir: Path = MOCK_GOV_DIR) -> None:
    """Put every demo company's, and every supplier's, ACRA records and every demo company's
    IRAS records into the mock service's data."""
    _upsert(mock_dir / "acra.json", "companies", "uen",
            [*(acra_record(c) for c in companies()), *(demo_trade.acra_record(s) for s in suppliers())])
    _upsert(mock_dir / "iras.json", "notices", "id_number", [n for c in companies() for n in iras_records(c)])


def generate(packs_dir: str | Path = PACKS_DIR, mock_dir: Path = MOCK_GOV_DIR) -> None:
    """Rebuild every demo pack and the demo companies' official records."""
    for case in CASES:
        build_pack(case, Path(packs_dir) / case.name)
    write_records(mock_dir)


if __name__ == "__main__":
    generate()
    print(f"Wrote {len(CASES)} demo packs to {PACKS_DIR}/ and their records to {MOCK_GOV_DIR}/")
