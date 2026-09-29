"""
gldb/privacy.py
---------------
Privacy utilities for the gldb-prototype.

Personal identifiers must be masked before appearing in Evidence, UI, or logs.
Full identifier values must never appear in logs/.
"""

from __future__ import annotations


def mask_id(value: str | None) -> str | None:
    """Mask a personal identifier, keeping the first and last 4 characters.

    Rules:
    - None  → None
    - len <= 5 → all "*"
    - otherwise → first char + "*" * (len - 5) + last 4 chars

    Examples:
        mask_id("S1234567D")  → "S****567D"   (9 chars)
        mask_id("A123")       → "****"         (4 chars, <= 5)
        mask_id(None)         → None
    """
    if value is None:
        return None
    n = len(value)
    if n <= 5:
        return "*" * n
    return value[0] + "*" * (n - 5) + value[-4:]
