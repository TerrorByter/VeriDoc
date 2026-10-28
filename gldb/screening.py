"""
gldb/screening.py
-----------------
Deterministic name screening against the sanctions list.

Names are normalised (case, punctuation, legal suffixes such as Pte/Ltd/
Limited, word order) and compared with a similarity ratio, against each listed
name and its aliases. Unlike an LLM judgement, the same names always give the
same score, so every hit can be explained and reproduced. A hit is a possible
match for a person to dispose of, never a conclusion. Where the case gives a
person's date of birth and the list gives the entry's, the two settle a name
match either way (birth_date_fits).
"""

from __future__ import annotations

import re
import string
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache
from typing import NamedTuple

from gldb.config import SCREENING_MATCH_THRESHOLD
from gldb.reference import (
    HighRiskCountry,
    WatchlistEntry,
    high_risk_countries,
    watchlist,
)

#: Legal-form words ignored when comparing entity names
_LEGAL_SUFFIXES = frozenset({
    "PTE", "PRIVATE", "LTD", "LIMITED", "CO", "COMPANY", "INC", "CORP",
    "CORPORATION", "LLC", "PLC", "BHD", "SDN",
})
_PUNCTUATION = str.maketrans({c: " " for c in string.punctuation})


def screening_key(name: str) -> str:
    """Upper-case words, punctuation and legal suffixes removed, sorted."""
    words = name.upper().translate(_PUNCTUATION).split()
    return " ".join(sorted(w for w in words if w not in _LEGAL_SUFFIXES))


def _ratio(x: str, y: str) -> float:
    """0-1 similarity of two screening keys (symmetric)."""
    x, y = sorted((x, y))
    return SequenceMatcher(None, x, y).ratio()


def similarity(a: str, b: str) -> float:
    """0-1 similarity of two names after normalisation (symmetric)."""
    return _ratio(screening_key(a), screening_key(b))


class Match(NamedTuple):
    entry: WatchlistEntry
    score: float
    #: The listed name or alias that scored
    matched: str


@lru_cache(maxsize=1)
def _keyed(version: str) -> list[tuple[str, str, WatchlistEntry]]:
    """Each screened name of the watchlist with its key, made once per version."""
    return [(key, listed, entry) for entry in watchlist().entries for listed in entry.names()
            if (key := screening_key(listed))]


def screen(name: str) -> list[Match]:
    """Watchlist entries matching *name* at or above the threshold, each
    with the name or alias that matched best."""
    key = screening_key(name)
    best: dict[str, Match] = {}
    for listed_key, listed, entry in _keyed(watchlist().version):
        # The ratio is at most 2 * the shorter length / both lengths: a name that
        # differs too much in length cannot reach the threshold, so is not compared
        if not key or 2 * min(len(key), len(listed_key)) < SCREENING_MATCH_THRESHOLD * (len(key) + len(listed_key)):
            continue
        score = _ratio(key, listed_key)
        if score >= SCREENING_MATCH_THRESHOLD and score > (best[entry.reference].score if entry.reference in best else 0):
            best[entry.reference] = Match(entry, score, listed)
    return list(best.values())


_LISTED_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_LISTED_YEARS = re.compile(r"(\d{4})(?:-(\d{4}))?")


def _born_as_listed(born: date, listed: str) -> bool:
    """Whether *born* fits one date of birth as the UN gives it: a day, a year
    (often approximate, so one either side fits) or a range of years. A form
    it does not know fits, so it never discounts a match."""
    if _LISTED_DAY.fullmatch(listed):
        return born.isoformat() == listed
    years = _LISTED_YEARS.fullmatch(listed)
    if years is None:
        return True
    first, last = years.groups()
    if last is None:
        return abs(born.year - int(first)) <= 1
    return int(first) <= born.year <= int(last)


def birth_date_fits(born: date | None, entry: WatchlistEntry) -> bool | None:
    """True when *born* fits one of the entry's dates of birth, False when it
    fits none, None when either side has no date to compare."""
    if born is None or not entry.birth_dates:
        return None
    return any(_born_as_listed(born, listed) for listed in entry.birth_dates)


def country_key(text: str) -> str:
    """A country as a form writes it, to compare by: no accents, case,
    apostrophes, other punctuation or extra spacing ("&" is "and")."""
    plain = unicodedata.normalize("NFKD", text.replace("’", "'").replace("'", "")).encode("ascii", "ignore").decode()
    return " ".join(plain.upper().replace("&", " AND ").translate(_PUNCTUATION).split())


@lru_cache(maxsize=1)
def _country_index(version: str) -> dict[str, HighRiskCountry]:
    """Every spelling of a listed country, by its key, made once per version of the list."""
    return {country_key(spelling): country for country in high_risk_countries().countries
            for spelling in (country.name, *country.aliases)}


def high_risk_country(written: str | None) -> HighRiskCountry | None:
    """The FATF-listed country that *written* names, if any. The match is exact
    after normalising: country names are a closed list, where a near match
    would join Niger to Nigeria."""
    return _country_index(high_risk_countries().version).get(country_key(written)) if written else None
