"""
tests/text_pdfs.py
------------------
Shared helper: a text PDF built on the fly, one line of text per row, so
parser tests do not depend on the team's sample files being present.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

LEFT = 40
TOP = 800
LEADING = 12


def text_pdf(tmp_path: Path, name: str, pages: list[list[str]]) -> Path:
    """A PDF with one page per list of lines, each line on its own row."""
    path = tmp_path / name
    canvas = Canvas(str(path), pagesize=A4)
    canvas.setFont("Helvetica", 8)
    for lines in pages:
        for row, line in enumerate(lines):
            canvas.drawString(LEFT, TOP - LEADING * row, line)
        canvas.showPage()
    canvas.save()
    return path
