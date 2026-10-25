"""
tests/test_form_pages.py
------------------------
A finding on a form points at the page the value is on: the sample entity
form keeps its tax rows on page 2 and its signatures on page 6. Uses the
copy of the team's sample in data/templates/sample/. No AWS calls.
"""

from __future__ import annotations

from pathlib import Path

from gldb.checks import check_acra_entity, check_acra_signatories
from gldb.extract_stub import parse_entity_self_cert
from tests.acma_records import acra, pack

ENTITY = Path(__file__).parent.parent / "data" / "templates" / "sample" / "ACMA ENTITY.pdf"


def test_signatories_and_tax_rows_carry_their_page() -> None:
    cert = parse_entity_self_cert("ACMA ENTITY", ENTITY)
    assert [(s.name, s.page) for s in cert.signatories] == [("MATTHEW TAN", 6), ("TAN BEE HUAT", 6)]
    assert [(r.tin, r.page) for r in cert.tax_rows] == [("196500233E", 2)]


def test_findings_on_the_form_point_at_that_page() -> None:
    cert = parse_entity_self_cert("ACMA ENTITY", ENTITY)
    (signatory,) = check_acra_signatories(pack(entity_self_certs=[cert], acra_profiles=[acra()]))
    assert (signatory.quote, signatory.page) == ("MATTHEW TAN", 6)
    (tin,) = check_acra_entity(pack(entity_self_certs=[cert], acra_profiles=[acra(uen="199999999Z")]))
    assert (tin.quote, tin.page) == ("196500233E", 2)
