"""
tests/acma_records.py
---------------------
Shared builders: the team's ACMA sample as extracted records (ACRA profile,
submitted financials, the entity and individual forms, the chairman's
notice), each overridable field by field.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from gldb.schemas import (
    AcraFinancials,
    AcraOfficer,
    AcraProfile,
    Address,
    EntitySelfCert,
    ExtractedPack,
    FinancialStatementSummary,
    IndividualSelfCert,
    NoticeOfAssessment,
    Signatory,
    TaxResidencyRow,
)

D = Decimal
UNIT = D("1000000")
FIGURES = {
    "total_assets": D("6.749"), "total_current_assets": D("6.166"), "total_liabilities": D("5.657"),
    "total_current_liabilities": D("5.416"), "retained_earnings": D("-194.497"), "revenue": D("7.935"),
    "profit_before_tax": D("-1.541"), "profit_after_tax": D("-1.548"), "ebit": D("-1.499"),
    "operating_cashflow": D("0.371"), "investing_cashflow": D("-0.122"), "financing_cashflow": D("-0.137"),
}


def acra(officers: list[AcraOfficer] | None = None, financials: AcraFinancials | None = None, **kw) -> AcraProfile:
    fields = {
        "doc_id": "ACMA_ACRA", "company_name": "ACMA LTD.", "uen": "196500233E",
        "incorporation_date": date(1965, 10, 9), "status": "LIVE COMPANY",
        "registered_address": "19 JURONG PORT ROAD (619093)", "issued_on": date(2026, 10, 4),
        "officers": officers if officers is not None else [
            AcraOfficer(name="TAN BEE HUAT", id_number="S2015888E", position="CHAIRMAN", appointed=date(2020, 10, 11)),
        ],
        "shareholders": [], "financials": financials,
    }
    return AcraProfile(**{**fields, **kw})


def acra_financials(**kw) -> AcraFinancials:
    period = {"period_start": date(2025, 1, 1), "period_end": date(2025, 12, 31), "currency": "SGD"}
    return AcraFinancials(**{**period, **FIGURES, **kw})


def fs(**kw) -> FinancialStatementSummary:
    scaled = {field: value * UNIT for field, value in FIGURES.items()}
    fields = {
        "doc_id": "Submitted financials", "financial_year_end": date(2025, 12, 31), "total_equity": D("1092000"),
        "figure_pages": {"revenue": 1, "total_assets": 2, "operating_cashflow": 3},
        "source_quotes": {"revenue": "Total Revenue 7.935", "total_assets": "Total Assets 6.749"},
        **scaled,
    }
    return FinancialStatementSummary(**{**fields, **kw})


def pack(**kw) -> ExtractedPack:
    fields = {"case_id": "t", "docs": [], "invoices": [], "bank_statements": [], "financial_statements": [],
              "extraction_mode": "stub"}
    return ExtractedPack(**{**fields, **kw})


def cert(**kw) -> EntitySelfCert:
    fields = {
        "doc_id": "ACMA ENTITY", "template_id": None, "legal_name": "ACMA LTD.",
        "country_of_incorporation": "SINGAPORE",
        "registered_address": Address(street="19 JURONG PORT ROAD", postal_code="619093", country="SINGAPORE"),
        "mailing_different": False, "mailing_address": Address(), "mailing_explanation": None,
        "branch_ticked": [], "hq_jurisdiction": None, "us_person_ticked": [], "us_tin": None,
        "tax_rows": [TaxResidencyRow(row=1, country="SINGAPORE", tin="196500233E", reasons_ticked=[], explanation=None)],
        "entity_types_ticked": [], "giin_parts": [], "controlling_persons": [],
        "signatories": [Signatory(slot=1, name="TAN BEE HUAT", designation="CHAIRMAN", date_raw=None, date=None,
                                  signature_field_signed=False)],
        "raw_fields": {},
    }
    return EntitySelfCert(**{**fields, **kw})


def noa(**kw) -> NoticeOfAssessment:
    fields = {"doc_id": "acma_NOA", "name": "MR TAN BEE HUAT", "id_number": "S2015888E", "year_of_assessment": 2025,
              "postal_code": "987654"}
    return NoticeOfAssessment(**{**fields, **kw})


def indiv(**kw) -> IndividualSelfCert:
    fields = {
        "doc_id": "ACMA INDIV", "template_id": None, "full_name": "TAN BEE HUAT", "date_of_birth": None,
        "place_of_birth": None, "residential_address": Address(postal_code="987654"), "mailing_different": False,
        "mailing_address": Address(), "mailing_explanation": None, "us_status_ticked": [], "us_tin_parts": [],
        "tax_rows": [], "declaration_name": "TAN BEE HUAT", "declaration_id_number": "S2015888E ",
        "declaration_date": None, "signature_field_signed": False, "raw_fields": {},
    }
    return IndividualSelfCert(**{**fields, **kw})


def chairman(**kw) -> AcraProfile:
    officer = AcraOfficer(name="TAN BEE HUAT", id_number="S2015888E", position="CHAIRMAN", appointed=date(2020, 10, 11),
                          **{"postal_code": "987654", **kw})
    return acra(officers=[officer])
