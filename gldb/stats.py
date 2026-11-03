"""
gldb/stats.py
-------------
How far a rate measured on few cases can be trusted. On 14 planted attacks,
"14 of 14" is consistent with a true detection rate as low as about 78%: the
interval says so where a bare 1.00 does not.
"""

from __future__ import annotations

from math import sqrt

#: 95% two-sided
Z_95 = 1.959964


def wilson(successes: int, trials: int, z: float = Z_95) -> tuple[float, float]:
    """The Wilson score interval for *successes* of *trials*: unlike the
    normal approximation it stays within 0 to 1 and is sound for small counts
    and for rates of 0 or 1. (0, 1) when there are no trials."""
    if trials <= 0:
        return 0.0, 1.0
    p = successes / trials
    centre = p + z * z / (2 * trials)
    spread = z * sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    scale = 1 + z * z / trials
    return max(0.0, (centre - spread) / scale), min(1.0, (centre + spread) / scale)


def counted(successes: int, trials: int) -> str:
    """'14 of 14 (95% interval 0.78-1.00)': the count, and how far it can be trusted."""
    low, high = wilson(successes, trials)
    return f"{successes} of {trials} (95% interval {low:.2f}-{high:.2f})"
