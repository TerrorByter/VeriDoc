"""
gldb/official.py
----------------
Fetches a case's official records from the sources a bank queries itself
instead of taking the applicant's copy: ACRA's record of every company the
documents name, and IRAS's latest Notice of Assessment for every guarantor
and individual declarant. In production these are the ACRA and Myinfo APIs;
in the prototype, mock_gov serves them over HTTP the same way.

The checks hold the applicant's documents to these records, and an uploaded
copy of a record to the original. A service that does not answer is recorded
on the case, never raised, so the run completes and reports the gap.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from gldb.checks import registered_entities
from gldb.config import OFFICIAL_RECORDS_TIMEOUT_S, OFFICIAL_RECORDS_URL
from gldb.schemas import ACRA_DOC, IRAS_DOC, AcraProfile, ExtractedPack, NoticeOfAssessment, OfficialRecords


class OfficialRecordsError(Exception):
    """The official records service did not answer, or answered wrongly."""


_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=OFFICIAL_RECORDS_URL, timeout=OFFICIAL_RECORDS_TIMEOUT_S)
    return _client


def _get(path: str, **params: str) -> Any:
    """The JSON at *path*; None when the service has no such record (404)."""
    try:
        response = _http().get(path, params=params or None)
    except httpx.HTTPError as exc:
        raise OfficialRecordsError(f"the official records service ({OFFICIAL_RECORDS_URL}) did not answer: {exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise OfficialRecordsError(f"the official records service answered {path} with {response.status_code}")
    return response.json()


def company(uen: str) -> AcraProfile | None:
    """ACRA's record of the company with *uen*; None if it has none."""
    found = _get(f"/acra/companies/{quote(uen.strip(), safe='')}")
    return None if found is None else AcraProfile.model_validate({**found, "doc_id": ACRA_DOC})


def search(name: str) -> list[AcraProfile]:
    """ACRA's records of the companies that may be *name*: candidates only,
    the caller decides which (if any) is a match."""
    return [found for c in _get("/acra/companies", name=name) or [] if (found := company(c["uen"])) is not None]


def notice(id_number: str) -> NoticeOfAssessment | None:
    """IRAS's latest Notice of Assessment for the NRIC or FIN; None if it has none."""
    found = _get(f"/iras/notices/{quote(id_number.strip(), safe='')}")
    return None if found is None else NoticeOfAssessment.model_validate({**found, "doc_id": IRAS_DOC})


def applicant_uen(pack: ExtractedPack) -> str | None:
    """The applicant's UEN: the application's, else the Singapore tax number
    on the entity self-certification (an entity's UEN), else an uploaded ACRA
    profile's, else the financial statements'."""
    if pack.manifest is not None:
        return pack.manifest.uen
    for cert in pack.entity_self_certs:
        for row in cert.tax_rows:
            if (row.country or "").strip().upper() == "SINGAPORE" and row.tin and row.tin.strip():
                return row.tin.strip()
    return next((p.uen for p in pack.acra_profiles), None) or next((f.uen for f in pack.financial_statements if f.uen), None)


def _people(pack: ExtractedPack) -> list[str]:
    """NRIC/FIN of everyone whose tax record the case relies on: the
    guarantors, the individual declarants and anyone an uploaded notice is for."""
    ids = [g.id_number for g in pack.manifest.guarantors] if pack.manifest else []
    ids += [c.declaration_id_number for c in pack.individual_self_certs if c.declaration_id_number]
    ids += [n.id_number for n in pack.notices_of_assessment]
    unique = {"".join(i.split()).upper(): i.strip() for i in reversed(ids)}
    return sorted(unique.values())


def fetch(pack: ExtractedPack) -> OfficialRecords:
    """Every official record the case needs, as of now."""
    retrieved = datetime.now(timezone.utc)
    applicant = applicant_uen(pack)
    uens = dict.fromkeys(u.strip() for u in [applicant, *(e[3] for e in registered_entities(pack))] if u)
    try:
        versions = {name: source["version"] for name, source in (_get("/") or {}).items()}
        companies = {uen: company(uen) for uen in uens}
        people = {person: notice(person) for person in _people(pack)}
    except OfficialRecordsError as exc:
        return OfficialRecords(retrieved_at=retrieved, applicant_uen=applicant, error=str(exc))
    return OfficialRecords(
        retrieved_at=retrieved, versions=versions, applicant_uen=applicant, companies=companies,
        notices=[n for n in people.values() if n is not None],
        no_notice=[person for person, n in people.items() if n is None],
    )
