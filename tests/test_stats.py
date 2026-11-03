"""
tests/test_stats.py
-------------------
The Wilson interval: known values, and the edges a bare rate hides.
"""

from __future__ import annotations

import pytest

from gldb.stats import wilson


def test_all_of_few_is_not_certainty() -> None:
    """14 of 14 is consistent with a true rate of about 78%."""
    low, high = wilson(14, 14)
    assert low == pytest.approx(0.7847, abs=1e-4) and high == pytest.approx(1.0)


def test_none_of_few_is_not_zero_for_certain() -> None:
    low, high = wilson(0, 3)
    assert low == 0.0 and high == pytest.approx(0.5615, abs=1e-4)


def test_a_middle_rate_is_bracketed() -> None:
    low, high = wilson(2, 3)
    assert low == pytest.approx(0.2077, abs=1e-4) and high == pytest.approx(0.9385, abs=1e-4)


def test_more_cases_narrow_the_interval() -> None:
    assert wilson(95, 100)[0] > wilson(19, 20)[0]


def test_no_trials_says_nothing() -> None:
    assert wilson(0, 0) == (0.0, 1.0)
