"""
mock_gov/app.py
---------------
A stand-in for the government records a bank fetches itself rather than take
the applicant's copy: ACRA's company register (BizFile) and IRAS's Notices of
Assessment (which Myinfo returns with the person's consent). The pipeline
calls it over HTTP, as it would call the real services.

Run:  uvicorn mock_gov.app:app --port 8001

The data is data/mock_gov/acra.json and iras.json, edited by hand. Each is
read on every request, so an edit shows at once. GET / gives each data set's
version, which a case records with what it fetched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

#: The records this service serves. A held-out run starts it with
#: MOCK_GOV_DATA_DIR pointing at the held-out set's own records, so its
#: companies never enter the demo data.
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "mock_gov"

#: Words of a company name too common to search by
_COMMON = frozenset({"PTE", "PRIVATE", "LTD", "LIMITED", "CO", "COMPANY", "THE", "AND"})

app = FastAPI(title="Mock ACRA and IRAS records")


def _data_dir() -> Path:
    """The directory the service reads on each request: MOCK_GOV_DATA_DIR if
    set, else the demo records."""
    configured = os.environ.get("MOCK_GOV_DATA_DIR")
    return Path(configured) if configured else DEFAULT_DATA_DIR


def _load(name: str) -> dict[str, Any]:
    return json.loads((_data_dir() / name).read_text(encoding="utf-8"))


def _key(text: str) -> str:
    return "".join(text.split()).upper()


def _words(name: str) -> set[str]:
    return {w for w in "".join(c if c.isalnum() else " " for c in name.upper()).split() if w not in _COMMON}


@app.get("/")
def sources() -> dict[str, dict[str, str]]:
    """Each data set's source, version and as-of date."""
    return {
        name: {k: data[k] for k in ("source", "version", "as_of")}
        for name, data in (("acra", _load("acra.json")), ("iras", _load("iras.json")))
    }


@app.get("/acra/companies/{uen}")
def company(uen: str) -> dict[str, Any]:
    """ACRA's record of the company with *uen*: profile, officers,
    shareholders and its latest filed financial statements."""
    found = next((c for c in _load("acra.json")["companies"] if _key(c["uen"]) == _key(uen)), None)
    if found is None:
        raise HTTPException(404, f"no company with UEN {uen}")
    return found


@app.get("/acra/companies")
def search(name: str) -> list[dict[str, str]]:
    """Companies whose name shares a distinctive word with *name*, as
    candidates for the caller to match (UEN and name only)."""
    wanted = _words(name)
    return [{"uen": c["uen"], "company_name": c["company_name"]}
            for c in _load("acra.json")["companies"] if wanted & _words(c["company_name"])]


@app.get("/iras/notices/{id_number}")
def notice(id_number: str) -> dict[str, Any]:
    """The latest Notice of Assessment IRAS holds for the NRIC or FIN."""
    held = [n for n in _load("iras.json")["notices"] if _key(n["id_number"]) == _key(id_number)]
    if not held:
        raise HTTPException(404, "no notice of assessment for that person")
    return max(held, key=lambda n: n["year_of_assessment"])
