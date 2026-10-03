"""
gldb/viewer.py
--------------
Evidence drill-down: a document page as an image, and where a quote sits on
it, so an analyst can see each finding in the document itself.

Pages are rendered on the server and quotes located as boxes relative to
the page (0-1), drawn over the image by the browser. The browser never needs
the PDF or a PDF engine.

Quotes are found in the PDF's text layer, invisible text included, so hidden
text shows where it is hiding. A scanned page has no text layer: its quote
cannot be placed, and the viewer says so rather than guessing.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pdfplumber
from pydantic import BaseModel

from gldb.config import VIEWER_DPI


def _fraction(value: float, size: float) -> float:
    """*value* as a fraction of *size*, kept on the page (hidden text may be
    placed off it)."""
    return min(max(value / size, 0.0), 1.0)


class Box(BaseModel):
    """A highlight, as fractions of the page width and height."""

    x0: float
    top: float
    x1: float
    bottom: float


def render_page(path: Path, page: int) -> bytes:
    """PNG of 1-based *page*."""
    with pdfplumber.open(str(path)) as pdf:
        image = pdf.pages[page - 1].to_image(resolution=VIEWER_DPI).original
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def locate(path: Path, page: int, text: str) -> list[Box]:
    """Boxes for every occurrence of *text* on 1-based *page*, one per line
    of the match. Spacing and line breaks in *text* do not matter."""
    words = text.split()
    if not words:
        return []
    pattern = r"\s+".join(re.escape(w) for w in words)
    with pdfplumber.open(str(path)) as pdf:
        p = pdf.pages[page - 1]
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
