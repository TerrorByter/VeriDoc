"""
gldb/viewer.py
--------------
Evidence drill-down: a document page as an image, and where a quote sits on
it, so an analyst can see each finding in the document itself.

Pages are rendered on the server and quotes located as boxes relative to
the page (0-1), drawn over the image by the browser. The browser never needs
the PDF or a PDF engine.

Quotes are found in the PDF's text layer, invisible text included, so hidden
text shows where it is hiding. A scanned page has no text layer: there the
lines Textract read (with their positions) are searched instead, and a quote
within a line is narrowed in proportion to its place in the line. Without
either, the quote cannot be placed and the viewer says so.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pdfplumber
from gldb.config import VIEWER_DPI
from gldb.intake import contains_quote
from gldb.schemas import Box, OcrLine


def _fraction(value: float, size: float) -> float:
    """*value* as a fraction of *size*, kept on the page (hidden text may be
    placed off it)."""
    return min(max(value / size, 0.0), 1.0)


def render_page(path: Path, page: int) -> bytes:
    """PNG of 1-based *page*."""
    with pdfplumber.open(str(path)) as pdf:
        image = pdf.pages[page - 1].to_image(resolution=VIEWER_DPI).original
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


def locate(path: Path, page: int, text: str, ocr_lines: list[OcrLine] | None = None) -> list[Box]:
    """Boxes for every occurrence of *text* on 1-based *page*, one per line
    of the match. Spacing and line breaks in *text* do not matter. A page
    with no text layer is searched in *ocr_lines* (that page's OCR lines)."""
    words = text.split()
    if not words:
        return []
    pattern = r"\s+".join(re.escape(w) for w in words)
    with pdfplumber.open(str(path)) as pdf:
        p = pdf.pages[page - 1]
        if not p.chars:
            return _ocr_locate(ocr_lines or [], text)
        boxes = []
        for match in p.search(pattern, regex=True, return_chars=True):
            lines: dict[int, list[dict]] = {}
            for ch in match["chars"]:
                lines.setdefault(round(ch["top"]), []).append(ch)
            boxes += [
                Box(
                    x0=_fraction(min(c["x0"] for c in chars), p.width),
                    top=_fraction(min(c["top"] for c in chars), p.height),
                    x1=_fraction(max(c["x1"] for c in chars), p.width),
                    bottom=_fraction(max(c["bottom"] for c in chars), p.height),
                )
                for chars in lines.values()
            ]
    return boxes
