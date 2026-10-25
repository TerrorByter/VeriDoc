"""
tests/test_acra_profile.py
--------------------------
An ACRA Corporate Compliance and Financial Profile: the register's own
statement of a company. Its layout is fixed and machine-made, so the stub
extractor reads it by code; the LLM extractor reads the same fields. Profiles
are built on the fly from the sample's wording. No AWS calls.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from gldb import extract_llm
from gldb.extract_stub import parse_pack
from gldb.intake import intake
from gldb.llm_client import LLMResult
from gldb.schemas import AcraProfile
from tests.acra_profiles import FINANCIAL_PAGE, FIRST_PAGE, PEOPLE_PAGE, profile_pdf

_profile = profile_pdf


def _read(path: Path) -> AcraProfile:
    docs = intake("t", [path])
    assert [d.doc_type for d in docs] == ["acra_profile"]
    (profile,) = parse_pack("t", docs, [path], None).acra_profiles
    return profile


def test_the_company_is_read_from_the_first_page(tmp_path: Path) -> None:
    profile = _read(_profile(tmp_path))
    assert (profile.company_name, profile.uen, profile.status) == ("ACMA LTD.", "196500233E", "LIVE COMPANY")
    assert profile.incorporation_date == date(1965, 10, 9)
    assert profile.registered_address == "19 JURONG PORT ROAD (619093)"
    assert profile.issued_on == date(2026, 10, 4)


def test_officers_are_read_with_their_position(tmp_path: Path) -> None:
    (officer,) = _read(_profile(tmp_path)).officers
    assert (officer.name, officer.id_number, officer.position, officer.appointed) == (
        "TAN BEE HUAT", "S2015888E", "CHAIRMAN", date(2020, 10, 11),
    )


def test_an_officers_postal_code_is_read_from_the_address_under_them(tmp_path: Path) -> None:
    (officer,) = _read(_profile(tmp_path)).officers
    assert officer.postal_code == "987654"


def test_an_officer_with_no_address_does_not_borrow_the_next_ones(tmp_path: Path) -> None:
    people = [*PEOPLE_PAGE[:8], "LIM AH KOW S1234567D SINGAPORE DIRECTOR 3 JAN 2021", "CITIZEN", "Shareholder(s)",
              "34 GREEN WALK (123456)"]
    first, second = _read(_profile(tmp_path, people=people)).officers
    assert (first.postal_code, second.postal_code) == (None, None)


def test_a_position_of_two_words_is_kept_whole(tmp_path: Path) -> None:
    people = [line.replace("CHAIRMAN", "ALTERNATE DIRECTOR") for line in PEOPLE_PAGE]
    (officer,) = _read(_profile(tmp_path, people=people)).officers
    assert officer.position == "ALTERNATE DIRECTOR"


def test_every_officer_is_read(tmp_path: Path) -> None:
    people = [*PEOPLE_PAGE[:9], "LIM AH KOW S1234567D SINGAPORE DIRECTOR 3 JAN 2021", "CITIZEN", *PEOPLE_PAGE[9:]]
    assert [(o.name, o.position) for o in _read(_profile(tmp_path, people=people)).officers] == [
        ("TAN BEE HUAT", "CHAIRMAN"), ("LIM AH KOW", "DIRECTOR"),
    ]


def test_shareholders_are_read_with_their_shares(tmp_path: Path) -> None:
    (holder,) = _read(_profile(tmp_path)).shareholders
    assert (holder.name, holder.shares) == ("BOBBY DARYL LEE", 25_000_000)


def test_the_latest_financial_period_is_read(tmp_path: Path) -> None:
    financials = _read(_profile(tmp_path)).financials
    assert financials is not None
    assert (financials.period_start, financials.period_end, financials.currency) == (
        date(2025, 1, 1), date(2025, 12, 31), "SGD",
    )
    assert (financials.total_assets, financials.total_current_assets) == (Decimal("6.749"), Decimal("6.166"))
    assert (financials.total_liabilities, financials.total_current_liabilities) == (Decimal("5.657"), Decimal("5.416"))
    assert (financials.revenue, financials.ebit) == (Decimal("7.935"), Decimal("-1.499"))


def test_losses_in_brackets_are_negative_and_labels_may_wrap(tmp_path: Path) -> None:
    financials = _read(_profile(tmp_path)).financials
    assert financials is not None
    assert financials.retained_earnings == Decimal("-194.497")
    assert (financials.profit_before_tax, financials.profit_after_tax) == (Decimal("-1.541"), Decimal("-1.548"))
    assert (financials.operating_cashflow, financials.investing_cashflow, financials.financing_cashflow) == (
        Decimal("0.371"), Decimal("-0.122"), Decimal("-0.137"),
    )


def test_figures_marked_unavailable_are_empty(tmp_path: Path) -> None:
    financial = [line.replace("Total Current Assets 6.166 8.113 *", "Total Current Assets * *") for line in FINANCIAL_PAGE]
    financials = _read(_profile(tmp_path, financial=financial)).financials
    assert financials is not None
    assert (financials.total_current_assets, financials.total_assets) == (None, Decimal("6.749"))


def test_a_profile_without_financial_information_has_none(tmp_path: Path) -> None:
    assert _read(_profile(tmp_path, financial=["Notes"])).financials is None


def test_a_profile_without_people_has_none(tmp_path: Path) -> None:
    profile = _read(_profile(tmp_path, people=["Notes"]))
    assert (profile.officers, profile.shareholders) == ([], [])


@pytest.mark.parametrize("label", ["UEN", "Name of Company", "Incorporation Date", "Status of Company",
                                   "Registered Office Address", "Date"])
def test_a_profile_missing_a_header_field_is_refused(tmp_path: Path, label: str) -> None:
    first = [line for line in FIRST_PAGE if not line.startswith(f"{label}:") and not line.startswith(f"{label} :")]
    with pytest.raises(ValueError, match="ACMA_ACRA.*cannot read the ACRA profile"):
        _read(_profile(tmp_path, first=first))


def test_the_llm_extractor_reads_the_same_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = _profile(tmp_path)
    docs = intake("t", [path])
    expected = parse_pack("t", docs, [path], None).acra_profiles
    purposes: list[str] = []

    def fake(system: str, user: str, *, purpose: str, case_id: str, schema: dict) -> LLMResult:
        purposes.append(purpose)
        data = json.loads(expected[0].model_dump_json(exclude={"doc_id", "page"}))
        return LLMResult(text="", data=data, input_tokens=1, output_tokens=1)

    monkeypatch.setattr(extract_llm, "converse", fake)
    assert extract_llm.parse_pack("t", docs, [path], None).acra_profiles == expected
    assert purposes == ["extract_acra_profile"]
