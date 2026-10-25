"""
tests/test_individual_form.py
-----------------------------
A guarantor's FATCA/CRS individual self-certification is an AcroForm: it is
read deterministically from its field values, never by an LLM. Forms are
filled from the GLDB template on the fly. No AWS calls.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from gldb import extract_llm, extract_stub
from gldb.data_factory import ENTITY_TEMPLATE, INDIVIDUAL_TEMPLATE
from gldb.extract_stub import parse_individual_self_cert
from gldb.form_maps import INDIVIDUAL_FIELD_MAP, fill_form
from gldb.intake import intake

pytestmark = pytest.mark.skipif(not INDIVIDUAL_TEMPLATE.exists(), reason="GLDB AcroForm templates not present")

F = INDIVIDUAL_FIELD_MAP
ADDRESS = F["residential_address"]
TAX_ROW = F["tax_rows"][0]

VALUES = {
    F["full_name"]: "TAN BEE HUAT",
    F["date_of_birth"]: "20/05/1982",
    F["place_of_birth"]: "SINGAPORE",
    ADDRESS["block"]: "123",
    ADDRESS["street"][0]: "HOUGANG AVENUE 3",
    ADDRESS["floor"]: "15",
    ADDRESS["unit"]: "243",
    ADDRESS["city_state"]: "SINGAPORE",
    ADDRESS["postal_code"]: "478236",
    ADDRESS["country"]: "SINGAPORE",
    F["us_status"]["not_us_person"]: "/Yes",
    TAX_ROW["country"]: "SINGAPORE",
    TAX_ROW["tin"]: "S2015888E ",
    TAX_ROW["A"]: "/Yes",
    F["declaration"]["name"]: "TAN BEE HUAT",
    F["declaration"]["id_number"]: "S2015888E ",
    F["declaration"]["date"]: "01/10/2026",
}


def _form(tmp_path: Path, values: dict[str, str] = VALUES, name: str = "ACMA INDIV.pdf") -> Path:
    path = tmp_path / name
    fill_form(INDIVIDUAL_TEMPLATE, path, values, mod_date="D:20261001000000Z")
    return path


def test_identification_is_read_from_the_fields(tmp_path: Path) -> None:
    cert = parse_individual_self_cert("indiv", _form(tmp_path))
    assert (cert.full_name, cert.date_of_birth, cert.place_of_birth) == ("TAN BEE HUAT", date(1982, 5, 20), "SINGAPORE")
    assert cert.residential_address.model_dump(exclude_none=True) == {
        "block": "123", "street": "HOUGANG AVENUE 3", "floor": "15", "unit": "243",
        "city_state": "SINGAPORE", "postal_code": "478236", "country": "SINGAPORE",
    }
    assert not cert.mailing_different


def test_us_status_and_tax_residency_are_read(tmp_path: Path) -> None:
    cert = parse_individual_self_cert("indiv", _form(tmp_path))
    assert cert.us_status_ticked == ["not_us_person"]
    (row,) = cert.tax_rows
    assert (row.row, row.country, row.tin, row.reasons_ticked) == (1, "SINGAPORE", "S2015888E", ["A"])


def test_the_declaration_is_read(tmp_path: Path) -> None:
    cert = parse_individual_self_cert("indiv", _form(tmp_path))
    assert (cert.declaration_name, cert.declaration_id_number, cert.declaration_date) == (
        "TAN BEE HUAT", "S2015888E", date(2026, 10, 1),
    )
    assert not cert.signature_field_signed


def test_a_date_that_is_not_a_date_is_left_empty(tmp_path: Path) -> None:
    cert = parse_individual_self_cert("indiv", _form(tmp_path, {**VALUES, F["date_of_birth"]: "May 1982"}))
    assert cert.date_of_birth is None


def test_the_template_is_identified_by_its_fields(tmp_path: Path) -> None:
    assert parse_individual_self_cert("indiv", _form(tmp_path, name="renamed.pdf")).template_id == INDIVIDUAL_TEMPLATE.stem


@pytest.mark.skipif(not ENTITY_TEMPLATE.exists(), reason="GLDB AcroForm templates not present")
def test_another_form_is_not_the_individual_template() -> None:
    assert parse_individual_self_cert("entity", ENTITY_TEMPLATE).template_id is None


@pytest.mark.parametrize("module", [extract_stub, extract_llm])
def test_both_extractors_read_the_form_without_an_llm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, module
) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("a form's contents never go to an LLM")

    monkeypatch.setattr(extract_llm, "converse", refuse)
    path = _form(tmp_path)
    docs = intake("t", [path])
    assert [d.doc_type for d in docs] == ["fatca_crs_individual"]
    (cert,) = module.parse_pack("t", docs, [path], None).individual_self_certs
    assert (cert.doc_id, cert.full_name) == ("ACMA INDIV", "TAN BEE HUAT")
