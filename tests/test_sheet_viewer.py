"""
tests/test_sheet_viewer.py
--------------------------
A workbook sheet is shown to the analyst as a page: rendered as an image, with
the row holding a finding's quote boxed. Workbooks are built on the fly. No
AWS calls.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from gldb.schemas import Box
from gldb.viewer import locate, render_page
from tests.workbooks import STATEMENTS, workbook


def _image(path: Path, page: int) -> Image.Image:
    return Image.open(io.BytesIO(render_page(path, page))).convert("L")


def _ink(image: Image.Image, box: Box) -> float:
    """Fraction of dark pixels inside *box* (fractions of the image)."""
    w, h = image.size
    region = image.crop((round(box.x0 * w), round(box.top * h), round(box.x1 * w), round(box.bottom * h)))
    return sum(1 for pixel in region.tobytes() if pixel < 128) / (region.width * region.height)


def test_a_sheet_renders_as_a_png_page_with_text_on_it(tmp_path: Path) -> None:
    path = workbook(tmp_path, STATEMENTS)
    data = render_page(path, 2)
    assert data.startswith(b"\x89PNG")
    image = _image(path, 2)
    assert image.width > 100 and image.height > 100
    assert _ink(image, Box(x0=0, top=0, x1=1, bottom=1)) > 0.01


def test_each_sheet_is_its_own_page(tmp_path: Path) -> None:
    path = workbook(tmp_path, STATEMENTS)
    assert render_page(path, 1) != render_page(path, 2) != render_page(path, 3)


def test_a_figures_row_is_boxed_where_it_is_drawn(tmp_path: Path) -> None:
    path = workbook(tmp_path, STATEMENTS)
    (box,) = locate(path, 2, "Total Assets 6.749")
    assert 0 <= box.x0 < box.x1 <= 1 and 0 <= box.top < box.bottom <= 1
    assert _ink(_image(path, 2), box) > 0.03


def test_rows_are_boxed_in_the_order_they_are_drawn(tmp_path: Path) -> None:
    path = workbook(tmp_path, STATEMENTS)
    (cash,) = locate(path, 2, "Cash And Equivalents 1.613")
    (assets,) = locate(path, 2, "Total Assets 6.749")
    assert cash.top < assets.top and cash.bottom <= assets.top + 1e-6


def test_a_value_or_label_alone_finds_its_row(tmp_path: Path) -> None:
    path = workbook(tmp_path, STATEMENTS)
    assert locate(path, 2, "6.749") == locate(path, 2, "Total Assets") == locate(path, 2, "Total   Assets 6.749")


def test_a_quote_in_two_rows_is_boxed_in_both(tmp_path: Path) -> None:
    sheets = {"Balance sheet": [["Total Assets", 1], ["Total Liabilities", 1]]}
    assert len(locate(workbook(tmp_path, sheets), 1, "1")) == 2


def test_text_the_sheet_does_not_hold_gives_no_boxes(tmp_path: Path) -> None:
    path = workbook(tmp_path, STATEMENTS)
    assert locate(path, 2, "Total Assets 9.999") == []
    assert locate(path, 1, "Total Assets") == []
    assert locate(path, 2, "   ") == []
