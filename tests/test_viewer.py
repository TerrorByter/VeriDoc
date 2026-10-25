"""
tests/test_viewer.py
--------------------
Page rendering and quote location for the evidence drill-down. No AWS calls.
"""

from __future__ import annotations

import io
from pathlib import Path

import pypdf
import pytest
from PIL import Image, ImageChops
from pypdf.generic import RectangleObject
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from gldb.viewer import locate, render_page
from tests.statement_forms import END, ROWS, statement_form

PACKS = Path(__file__).parent / "fixtures" / "packs"
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


def _image(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("L")


def test_a_fillable_form_renders_its_field_values(tmp_path: Path) -> None:
    """pdfium draws form fields only once its form environment is initialised."""
    filled = statement_form(tmp_path, [*ROWS, END], name="filled.pdf")
    emptied = statement_form(tmp_path, [("", "", "", "", "")] * 3, header={"F01008": ""}, name="emptied.pdf")
    assert ImageChops.difference(_image(render_page(filled, 1)), _image(render_page(emptied, 1))).getbbox()


def test_a_form_field_value_is_located_at_its_field(tmp_path: Path) -> None:
    path = statement_form(tmp_path, [*ROWS, END])
    (box,) = locate(path, 1, "Payment - Rent")
    page = pypdf.PdfReader(str(path)).pages[0]
    widget = next(a.get_object() for a in page["/Annots"] if a.get_object()["/T"] == "F02001b")
    x0, y0, x1, y1 = (float(v) for v in widget["/Rect"])
    width, height = float(page.mediabox.width), float(page.mediabox.height)
    assert (box.x0, box.top, box.x1, box.bottom) == pytest.approx(
        (x0 / width, (height - y1) / height, x1 / width, (height - y0) / height), abs=1e-3)


def test_a_value_split_across_spacing_is_still_located_in_its_field(tmp_path: Path) -> None:
    path = statement_form(tmp_path, [*ROWS, END])
    assert len(locate(path, 1, "Sales   Revenue")) == 1


def test_text_beside_a_form_is_still_located_in_the_text_layer(tmp_path: Path) -> None:
    assert len(locate(statement_form(tmp_path, [*ROWS, END]), 1, "STATEMENT OF ACCOUNT")) == 1


def test_a_value_no_field_holds_gives_no_boxes(tmp_path: Path) -> None:
    assert locate(statement_form(tmp_path, [*ROWS, END]), 1, "Payment - Salaries") == []


def test_boxes_follow_a_page_whose_box_does_not_start_at_the_origin(tmp_path: Path) -> None:
    """Canva-made pages, like the sample NOA, have a page box offset from (0, 0)."""
    plain = tmp_path / "plain.pdf"
    canvas = Canvas(str(plain), pagesize=A4)
    canvas.setFont("Helvetica", 12)
    canvas.drawString(72, 700, "Tax Payable $6,522.03")
    canvas.save()
    writer = pypdf.PdfWriter(clone_from=str(plain))
    shifted = RectangleObject([0, -20, 595.28, 821.89])
    writer.pages[0].mediabox = shifted
    writer.pages[0].cropbox = shifted
    path = tmp_path / "shifted.pdf"
    writer.write(str(path))

    (box,) = locate(path, 1, "$6,522.03")
    image = _image(render_page(path, 1))
    region = image.crop((int(box.x0 * image.width), int(box.top * image.height),
                         int(box.x1 * image.width), int(box.bottom * image.height)))
    ink = sum(1 for pixel in region.tobytes() if pixel < 128)
    assert ink / (region.width * region.height) > 0.05
