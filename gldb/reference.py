"""
gldb/reference.py
-----------------
Versioned reference data: the company register (a synthetic stand-in for
ACRA BizFile) and the sanctions list (a synthetic stand-in for the UN and MAS
targeted financial sanctions lists).

Every file carries a source, version and as-of date. Findings that use the
data quote the version, and the pipeline logs it, so a past case can be
replayed against the exact list it was screened with. In production these
come from the ACRA API and a screening vendor's feed.
"""

from __future__ import annotations

import json
from datetime import date
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from gldb.config import REFERENCE_DIR


class Officer(BaseModel):
    name: str
    role: str


class RegisteredEntity(BaseModel):
    name: str
    status: str            # "Live", "Struck Off", ...
    incorporated: date
    officers: list[Officer]


class Registry(BaseModel):
    source: str
    version: str
    as_of: date
    #: UEN -> registered entity
    entities: dict[str, RegisteredEntity]


class WatchlistEntry(BaseModel):
    name: str
    kind: Literal["entity", "individual"]
    list: str
    reference: str


class Watchlist(BaseModel):
    source: str
    version: str
    as_of: date
    entries: list[WatchlistEntry]


def _load(name: str) -> dict:
    return json.loads((Path(REFERENCE_DIR) / name).read_text(encoding="utf-8"))


@cache
def registry() -> Registry:
    return Registry.model_validate(_load("acra_registry.json"))


@cache
def watchlist() -> Watchlist:
    return Watchlist.model_validate(_load("sanctions.json"))


def versions() -> dict[str, str]:
    """Reference data versions, for the audit log."""
    return {"registry": registry().version, "sanctions": watchlist().version}
