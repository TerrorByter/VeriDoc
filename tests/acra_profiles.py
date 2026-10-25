"""
tests/acra_profiles.py
----------------------
Shared helper: an ACRA profile built on the fly from the sample's wording, so
tests do not depend on the team's sample files being present.
"""

from __future__ import annotations

from pathlib import Path

from tests.text_pdfs import text_pdf

FIRST_PAGE = [
    "Sample ACCOUNTING AND CORPORATE REGULATORY AUTHORITY",
    "(ACRA)",
    "Corporate Compliance and Financial Profile of",
    "ACMA LTD (196500233E)",
    "Date: 04 Oct 2026",
    "Name of Company : ACMA LTD.",
    "Former Name if any : China Auto Corporation",
    "Date of Change of Name : 15 APR 2013",
    "UEN : 196500233E",
    "Incorporation Date : 9 October 1965",
    "Company Type : Public Company Limited By Shares",
    "Status of Company : LIVE COMPANY",
    "Status Date : 30 JUN 2026",
    "Registered Office Address : 19 JURONG PORT ROAD (619093)",
    "Date of Address : 30 JUN 2026",
]
FINANCIAL_PAGE = [
    "Corporate Compliance and Financial Profile of",
    "Financial Information",
    "Date: 04 Oct 2026",
    "Financial Period 01 JAN 2025 TO 01 JAN 2024 TO *",
    "31 DEC 2025 31 DEC 2024",
    "Extracted From: # XBRL FS FOR PERIOD XBRL FS FOR PERIOD *",
    "ENDED 31 DEC 2025 ENDED 31 DEC 2024",
    "Company/Group Account COMPANY COMPANY *",
    "Currency SGD SGD *",
    "Balance Sheet",
    "Total Assets 6.749 8.827 *",
    "Total Current Assets 6.166 8.113 *",
    "Total Liabilities 5.657 6.187 *",
    "Total Current Liabilities 5.416 5.872 *",
    "Retained Earnings (Accumulated (194.497) (193.127) *",
    "Loss)",
    "Profit and Loss",
    "Revenue 7.935 9.481 *",
    "Profit(Loss) before tax from continuing (1.541) (0.645) *",
    "operations",
    "Profit(Loss) after tax from continuing (1.548) (0.68) *",
    "operations",
    "Profit(Loss) after tax from (1.548) 0.775 *",
    "discontinued operations",
    "EBIT (1.499) (1.034) *",
    "Cashflow Statement",
    "Net cashflow from (used in) operating 0.371 (1.816) *",
    "activities",
    "Net cashflow from (used in) investing (0.122) 5.216 *",
    "activities",
    "Net cashflow from (used in) financing (0.137) (4.819) *",
    "activities",
]
PEOPLE_PAGE = [
    "Corporate Compliance and Financial Profile of",
    "ACMA LTD (196500233E)",
    "Date: 04 Oct 2026",
    "Officer(s)",
    "Name Identification Nationality/ Position Date of",
    "Number Citizenship Appointment",
    "Address",
    "TAN BEE HUAT S2015888E SINGAPORE CHAIRMAN 11 OCT 2020",
    "CITIZEN",
    "123 BUKIT TIMAH STREET 45",
    "#12-234 SINGAPORE 987654",
    "Shareholder(s)",
    "Name Identification Nationality 2/ Number of Address",
    "Number Place of origin3 Shares Changed",
    "Address Currency",
    "BOBBY DARYL LEE WDWNMWRQY6 AUSTRIAN 25,000,000 20 OCT 2020",
    "EN4MFLG3BXN2 (ORDINARY)",
    "6QIZLGMD",
    "34 GREEN WALK HILLS ESTATE SINGAPORE SINGAPORE,",
    "(123456) DOLLARS",
]



def profile_pdf(
    tmp_path: Path, first: list[str] = FIRST_PAGE, people: list[str] = PEOPLE_PAGE,
    financial: list[str] = FINANCIAL_PAGE, name: str = "ACMA_ACRA.pdf",
) -> Path:
    return text_pdf(tmp_path, name, [first, ["Issued Share Capital"], financial, people])
