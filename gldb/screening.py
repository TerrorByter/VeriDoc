"""
gldb/screening.py
-----------------
Deterministic name screening against the sanctions list.

Names are normalised (case, punctuation, legal suffixes such as Pte/Ltd/
Limited, word order) and compared with a similarity ratio. Unlike an LLM
judgement, the same names always give the same score, so every hit can be
explained and reproduced. A hit is a possible match for a person to dispose
of, never a conclusion.
"""

from __future__ import annotations

import string
from difflib import SequenceMatcher

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


def similarity(a: str, b: str) -> float:
    """0-1 similarity of two names after normalisation (symmetric)."""
    x, y = sorted((screening_key(a), screening_key(b)))
    return SequenceMatcher(None, x, y).ratio()


def screen(name: str) -> list[tuple[WatchlistEntry, float]]:
    """Watchlist entries matching *name* at or above the threshold."""
    hits = [(entry, similarity(name, entry.name)) for entry in watchlist().entries]
    return [(entry, score) for entry, score in hits if score >= SCREENING_MATCH_THRESHOLD]
