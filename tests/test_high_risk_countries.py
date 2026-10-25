"""
tests/test_high_risk_countries.py
---------------------------------
FATF's lists of high-risk jurisdictions (data/reference/high_risk_countries.json),
how a country written on a form is matched to them, and the check on the
countries an application and its forms give for the registered address and for
tax residence. No AWS calls.
"""

from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path

import pytest

from gldb import demo_packs, reference, screening
from gldb.checks import RULE_GROUPS, PACK_DOC, check_high_risk_countries
from gldb.coverage import coverage
from gldb.intake import document_paths, load_manifest
from gldb.pipeline import assess, pack_from_folder
from gldb.schemas import Address, TaxResidencyRow
from gldb.scoring import score
from gldb.signal_library import BY_CHECK
from tests.acma_records import cert, indiv, pack

CLEAN = Path(__file__).parent / "fixtures" / "packs" / "clean"


def _row(country: str | None, row: int = 1, page: int = 2) -> TaxResidencyRow:
    return TaxResidencyRow(row=row, country=country, tin=None, reasons_ticked=[], explanation=None, page=page)


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------

def test_the_list_is_fatfs_with_its_version_and_date() -> None:
    countries = reference.high_risk_countries()
    assert countries.version and countries.as_of and "FATF" in countries.source
    assert Counter(c.fatf_list for c in countries.countries) == {"call_for_action": 3, "increased_monitoring": 22}
    assert {c.name for c in countries.countries if c.fatf_list == "call_for_action"} == {
        "Democratic People's Republic of Korea", "Iran", "Myanmar"}
    assert reference.versions()["high_risk_countries"] == countries.version


def test_no_spelling_points_at_two_countries() -> None:
    keys = [screening.country_key(n) for c in reference.high_risk_countries().countries for n in (c.name, *c.aliases)]
    assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------
# Matching what a form says
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("written", "key"), [
    ("Côte d'Ivoire", "COTE DIVOIRE"), ("Cote d’Ivoire", "COTE DIVOIRE"),
    ("  iran,  Islamic Republic of ", "IRAN ISLAMIC REPUBLIC OF"), ("Bosnia & Herzegovina", "BOSNIA AND HERZEGOVINA"),
])
def test_a_country_is_compared_without_accents_case_punctuation_or_spacing(written: str, key: str) -> None:
    assert screening.country_key(written) == key


@pytest.mark.parametrize(("written", "listed"), [
    ("IRAN", "Iran"), ("Iran (Islamic Republic of)", "Iran"), ("IR", "Iran"), ("Korea, North", "Democratic People's Republic of Korea"),
    ("dprk", "Democratic People's Republic of Korea"), ("Burma", "Myanmar"), ("Viet Nam", "Vietnam"), ("VN", "Vietnam"),
    ("Ivory Coast", "Côte d'Ivoire"), ("COTE D'IVOIRE", "Côte d'Ivoire"), ("Laos", "Lao PDR"),
    ("Lao People's Democratic Republic", "Lao PDR"), ("British Virgin Islands", "Virgin Islands (UK)"),
    ("DR Congo", "Democratic Republic of the Congo"), ("Syrian Arab Republic", "Syria"), ("Bosnia", "Bosnia and Herzegovina"),
])
def test_the_ways_a_form_writes_a_listed_country_are_found(written: str, listed: str) -> None:
    found = screening.high_risk_country(written)
    assert found is not None and found.name == listed


@pytest.mark.parametrize("written", [
    "SINGAPORE", "SG", "Hong Kong", "United States", "Nigeria", "Niger", "Sudan", "Guinea", "Virgin Islands (US)",
    "Korea", "Congo", "Virgin Islands", "", "   ", None,
])
def test_other_countries_and_ambiguous_names_are_not_matched(written: str | None) -> None:
    assert screening.high_risk_country(written) is None


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def test_a_singaporean_application_has_nothing_to_flag() -> None:
    assert check_high_risk_countries(pack(entity_self_certs=[cert()], individual_self_certs=[indiv()])) == []
    assert check_high_risk_countries(pack()) == []


def test_a_country_that_fatf_calls_for_action_against_is_critical() -> None:
    entity = cert(registered_address=Address(street="1 Azadi St", postal_code="1234", country="IRAN"), tax_rows=[_row("Iran")])
    (found,) = check_high_risk_countries(pack(entity_self_certs=[entity]))
    assert (found.check_id, found.severity, found.category, found.document, found.page) == (
        "HIGH_RISK_COUNTRY", "critical", "fraud_signal", "ACMA ENTITY", 1)
    assert found.quote == "IRAN" and "registered address" in found.explanation and "tax residence" in found.explanation
    assert "call for action" in found.explanation and reference.high_risk_countries().version in found.explanation
    assert score([found]).band == "High"  # alone, it puts the case on enhanced review


def test_a_country_under_increased_monitoring_is_high() -> None:
    entity = cert(tax_rows=[_row("SINGAPORE"), _row("VIET NAM", row=2)])
    (found,) = check_high_risk_countries(pack(entity_self_certs=[entity]))
    assert (found.severity, found.document, found.page, found.quote) == ("high", "ACMA ENTITY", 2, "VIET NAM")
    assert "increased monitoring" in found.explanation and "tax residence" in found.explanation
    assert score([found]).band == "Medium"


def test_an_individuals_tax_residence_is_checked() -> None:
    (found,) = check_high_risk_countries(pack(individual_self_certs=[indiv(tax_rows=[_row("Kenya", page=1)])]))
    assert (found.document, found.severity, found.page) == ("ACMA INDIV", "high", 1)


def test_the_applications_registered_address_is_checked() -> None:
    clean = pack_from_folder(CLEAN)
    assert clean.manifest is not None
    moved = clean.model_copy(update={"manifest": clean.manifest.model_copy(
        update={"registered_address": clean.manifest.registered_address.model_copy(update={"country": "Myanmar"})})})
    (found,) = check_high_risk_countries(moved)
    assert (found.document, found.page, found.severity) == (PACK_DOC, 1, "critical")
    assert "registered address" in found.explanation and "application" in found.explanation


def test_each_country_is_one_finding_per_document() -> None:
    entity = cert(registered_address=Address(country="IRAN"), tax_rows=[_row("IRAN"), _row("Iraq", row=2)])
    found = check_high_risk_countries(pack(entity_self_certs=[entity], individual_self_certs=[indiv(tax_rows=[_row("IRAN")])]))
    assert sorted((f.document, f.severity) for f in found) == [
        ("ACMA ENTITY", "critical"), ("ACMA ENTITY", "high"), ("ACMA INDIV", "critical")]


def test_the_check_and_its_signal_are_registered() -> None:
    group = next(g for g in RULE_GROUPS if g.name == "Registry & Screening")
    assert group.checks[-1] is check_high_risk_countries
    assert BY_CHECK["HIGH_RISK_COUNTRY"].signal_id == "COMP-031"


# ---------------------------------------------------------------------------
# Through the pipeline, from an edited PDF form
# ---------------------------------------------------------------------------

def test_a_country_written_on_the_form_is_found_and_traced_to_its_page(tmp_path: Path) -> None:
    folder = tmp_path / "case"
    shutil.copytree(Path("data/packs/clean"), folder)
    form = folder / "fatca_crs_entity.pdf"
    demo_packs._write_form(form, form.with_name("edited.pdf"), {"Country": "IRAN", "Country Jurisdiction of Tax Residence2": "VIET NAM"})
    form.unlink()
    form.with_name("edited.pdf").rename(form)

    result = assess("t", document_paths(folder), load_manifest(folder), team=None)
    found = {(e.severity, e.page, e.quote) for e in result.evidence if e.check_id == "HIGH_RISK_COUNTRY"}
    assert found == {("critical", 1, "IRAN"), ("high", 2, "VIET NAM")}
    assert result.risk.band == "High" and result.routing is not None and result.routing.edd_required
    assert coverage(result.evidence, result.pack) == 1.0
