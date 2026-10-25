"""
gldb/screening.py
-----------------
Deterministic name screening against the sanctions list.

Names are normalised (case, punctuation, legal suffixes such as Pte/Ltd/
Limited, word order) and compared with a similarity ratio, against each listed
name and its aliases. Unlike an LLM judgement, the same names always give the
same score, so every hit can be explained and reproduced. A hit is a possible
match for a person to dispose of, never a conclusion.
"""

from __future__ import annotations

import string
from difflib import SequenceMatcher
from functools import lru_cache
from typing import NamedTuple

from gldb.config import SCREENING_MATCH_THRESHOLD
from gldb.reference import WatchlistEntry, watchlist

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
