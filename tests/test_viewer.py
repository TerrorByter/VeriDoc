"""
tests/test_viewer.py
--------------------
Page rendering and quote location for the evidence drill-down. No AWS calls.
"""

from __future__ import annotations

from pathlib import Path

from gldb.viewer import locate, render_page

PACKS = Path(__file__).parent.parent / "data" / "packs"
INVOICE = PACKS / "tampered" / "invoice_INV-1002.pdf"


def test_page_renders_as_png() -> None:
    png = render_page(INVOICE, 1)
    assert png.startswith(b"\x89PNG") and len(png) > 10_000


def test_quote_is_located_as_page_relative_boxes() -> None:
    (box,) = locate(INVOICE, 1, "Total: SGD 48,505.00")
    assert 0 <= box.x0 < box.x1 <= 1 and 0 <= box.top < box.bottom <= 1


def test_spacing_and_line_breaks_do_not_matter() -> None:
    assert locate(INVOICE, 1, "Total:   SGD\n48,505.00")


def test_hidden_text_is_located_too() -> None:
    assert locate(INVOICE, 1, "SYSTEM NOTE TO AI REVIEWER")


def test_text_not_on_the_page_gives_no_boxes() -> None:
    assert locate(INVOICE, 1, "Total: SGD 99,999.00") == []
    assert locate(INVOICE, 1, "") == []


def test_scanned_page_has_no_text_layer_to_locate_in() -> None:
    assert locate(PACKS / "scanned" / "invoice_INV-1001.pdf", 1, "INV-1001") == []
