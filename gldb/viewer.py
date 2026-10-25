"""
gldb/viewer.py
--------------
Evidence drill-down: a document page as an image, and where a quote sits on
it, so an analyst can see each finding in the document itself.

Pages are rendered on the server and quotes located as boxes relative to
the page (0-1), drawn over the image by the browser. The browser never needs
the PDF or a PDF engine.

Quotes are found in the PDF's text layer, invisible text included, so hidden
text shows where it is hiding. A fillable form's values are not in the text
layer: there the form fields holding the quote are boxed. A scanned page has
no text layer: there the lines Textract read (with their positions) are
searched instead, and a quote within a line is narrowed in proportion to its
place in the line. Without any of these, the quote cannot be placed and the
viewer says so.

A workbook's sheet is a page too: it is drawn as a table, and the row holding
a quote is boxed.
"""

from __future__ import annotations

import io
import re
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import pdfplumber
import pypdf
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont

from gldb.config import SHEET_FONT_PX, SHEET_PAD_PX, VIEWER_DPI
from gldb.intake import contains_quote
from gldb.schemas import Box, OcrLine
from gldb.workbook import Sheet, cell_text, is_workbook, read_workbook


def _fraction(value: float, size: float) -> float:
    """*value* as a fraction of *size*, kept on the page (hidden text may be
    placed off it)."""
    return min(max(value / size, 0.0), 1.0)


_Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


class _SheetRow(NamedTuple):
    #: The row's cells, joined by spaces: what a quote is searched in
    text: str
    #: Where the row is drawn (left, top, right, bottom), in pixels
    box: tuple[int, int, int, int]
    #: Each cell's text, whether it is a figure, and the column it is in
    cells: list[tuple[str, bool, int]]


class _SheetLayout(NamedTuple):
    width: int
    height: int
    row_height: int
    #: Left edge of each column
    columns: list[int]
    rows: list[_SheetRow]


def _sheet_layout(sheet: Sheet, font: _Font) -> _SheetLayout:
    """Where a sheet's rows and columns sit when drawn as a table: one line
    per row that has cells, under a heading with the sheet's name."""
    shown = [
        [(cell_text(c), isinstance(c, Decimal)) for c in row]
        for row in sheet.rows if any(c is not None for c in row)
    ]
    widths = [
        round(max(font.getlength(row[i][0]) for row in shown if i < len(row))) + 2 * SHEET_PAD_PX
        for i in range(max(map(len, shown), default=0))
    ]
    columns = [sum(widths[:i]) for i in range(len(widths))]
    height = SHEET_FONT_PX + 2 * SHEET_PAD_PX
    rows = []
    for n, row in enumerate(shown, start=1):
        filled = [i for i, (text, _) in enumerate(row) if text]
        if not filled:
            continue
        top = n * height
        rows.append(_SheetRow(
            " ".join(row[i][0] for i in filled),
            (columns[filled[0]], top, columns[filled[-1]] + widths[filled[-1]], top + height),
            [(text, figure, i) for i, (text, figure) in enumerate(row) if text],
        ))
    return _SheetLayout(max(sum(widths), 1), (len(shown) + 1) * height, height, columns, rows)


def _font() -> _Font:
    return ImageFont.load_default(size=SHEET_FONT_PX)


def _sheet(path: Path, page: int) -> Sheet:
    return read_workbook(path)[page - 1]


def _render_sheet(path: Path, page: int) -> bytes:
    """PNG of 1-based sheet *page* drawn as a table, figures right-aligned."""
    sheet, font = _sheet(path, page), _font()
    layout = _sheet_layout(sheet, font)
    image = Image.new("RGB", (layout.width, layout.height), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, layout.width, layout.row_height), fill=(238, 240, 243))
    draw.text((SHEET_PAD_PX, SHEET_PAD_PX), sheet.name, fill=(20, 24, 31), font=font)
    for row in layout.rows:
        left, top, _, bottom = row.box
        draw.line((0, bottom, layout.width, bottom), fill=(222, 225, 230))
        for text, figure, column in row.cells:
            x: float = layout.columns[column] + SHEET_PAD_PX
            if figure:
                x = (layout.columns[column + 1] if column + 1 < len(layout.columns) else layout.width) \
                    - SHEET_PAD_PX - font.getlength(text)
            draw.text((x, top + SHEET_PAD_PX), text, fill=(20, 24, 31), font=font)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _sheet_boxes(path: Path, page: int, text: str) -> list[Box]:
    """Boxes for the rows of 1-based sheet *page* that contain *text*."""
    if not text.split():
        return []
    layout = _sheet_layout(_sheet(path, page), _font())
    return [
        Box(x0=left / layout.width, top=top / layout.height, x1=right / layout.width, bottom=bottom / layout.height)
        for row in layout.rows if contains_quote([row.text], text)
        for left, top, right, bottom in [row.box]
    ]


def render_page(path: Path, page: int) -> bytes:
    """PNG of 1-based *page*, with a fillable form's field values drawn (pdfium
    draws form fields only once its form environment is initialised). A
    workbook's sheet is drawn as a table."""
    if is_workbook(path):
        return _render_sheet(path, page)
    pdf = pdfium.PdfDocument(str(path))
    try:
        pdf.init_forms()
        image = pdf[page - 1].render(scale=VIEWER_DPI / 72, may_draw_forms=True).to_pil()
    finally:
        pdf.close()
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


#: Longest run of OCR lines one quote is matched across
_MAX_OCR_LINES = 3


def _ocr_locate(lines: list[OcrLine], text: str) -> list[Box]:
    """Boxes for the first, shortest run of OCR lines containing *text*."""
    squashed = " ".join(text.split())
    for span in range(1, _MAX_OCR_LINES + 1):
        for i in range(len(lines) - span + 1):
            run = lines[i:i + span]
            if not contains_quote([line.text for line in run], squashed):
                continue
            if span > 1:
                return [line.box for line in run]
            line = " ".join(run[0].text.split())
            start = line.index(squashed)
            b = run[0].box
            width = b.x1 - b.x0
            return [b.model_copy(update={
                "x0": round(b.x0 + width * start / len(line), 4),
                "x1": round(b.x0 + width * (start + len(squashed)) / len(line), 4),
            })]
    return []


def _field_boxes(path: Path, page: int, text: str) -> list[Box]:
    """Boxes of the form fields on 1-based *page* whose value contains *text*
    (spacing does not matter): a fillable form's values are not in the text layer."""
    wanted = " ".join(text.split())
    pdf_page = pypdf.PdfReader(str(path)).pages[page - 1]
    view = pdf_page.cropbox
    boxes = []
    for ref in pdf_page.annotations or []:
        field = ref.get_object()
        if field.get("/Subtype") != "/Widget" or wanted not in " ".join(str(field.get("/V", "")).split()):
            continue
        xa, ya, xb, yb = (float(v) for v in field["/Rect"])
        boxes.append(Box(
            x0=_fraction(min(xa, xb) - view.left, view.width), top=_fraction(view.top - max(ya, yb), view.height),
            x1=_fraction(max(xa, xb) - view.left, view.width), bottom=_fraction(view.top - min(ya, yb), view.height),
        ))
    return boxes


def locate(path: Path, page: int, text: str, ocr_lines: list[OcrLine] | None = None) -> list[Box]:
    """Boxes for every occurrence of *text* on 1-based *page*, one per line
    of the match. Spacing and line breaks in *text* do not matter. A page
    with no text layer is searched in *ocr_lines* (that page's OCR lines). In a
    workbook the page is a sheet and each box is a row."""
    if is_workbook(path):
        return _sheet_boxes(path, page, text)
    words = text.split()
    if not words:
        return []
    pattern = r"\s+".join(re.escape(w) for w in words)
    with pdfplumber.open(str(path)) as pdf:
        p = pdf.pages[page - 1]
        if not p.chars:
            return _ocr_locate(ocr_lines or [], text) or _field_boxes(path, page, text)
        boxes = []
        for match in p.search(pattern, regex=True, return_chars=True):
            lines: dict[int, list[dict]] = {}
            for ch in match["chars"]:
                lines.setdefault(round(ch["top"]), []).append(ch)
            # Characters are placed from the page's origin, which is not (0, 0) on every page
            boxes += [
                Box(
                    x0=_fraction(min(c["x0"] for c in chars) - p.bbox[0], p.width),
                    top=_fraction(min(c["top"] for c in chars) - p.bbox[1], p.height),
                    x1=_fraction(max(c["x1"] for c in chars) - p.bbox[0], p.width),
                    bottom=_fraction(max(c["bottom"] for c in chars) - p.bbox[1], p.height),
                )
                for chars in lines.values()
            ]
    return boxes or _field_boxes(path, page, text)
