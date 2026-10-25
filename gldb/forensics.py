"""
gldb/forensics.py
-----------------
PDF forensics primitives. Each function inspects one file and returns plain
findings; gldb.checks wraps them into Evidence for the Forensics rule group.

Detects, without any AWS call:
- hidden text (white, tiny or off-page): where injected instructions and
  invisible edits live. White text on a dark panel or image is a heading, not
  hidden: it counts as hidden only against a light backdrop;
- mixed-font tokens: a number whose digits are drawn in different fonts or
  sizes, the trace left by overlaying edited text;
- incremental updates: a PDF saved again after creation;
- amount mismatches between the text layer and what OCR sees on the page:
  a figure covered by a box, or one pasted over the original as an image.
  A fillable form keeps its figures in form fields, not in the text layer, so
  the comparison counts the field values (form_values) as stored text.
"""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import groupby
from pathlib import Path
from typing import Any

import pdfplumber
import pypdf
from PIL import Image
from pypdf.generic import TextStringObject

from gldb.config import (
    BACKDROP_DPI,
    HIDDEN_TEXT_MAX_SIZE_PT,
    TOKEN_GAP_FRACTION,
    WHITE_FILL_MIN,
    WHITE_TEXT_BACKDROP_MAX_LUMINANCE,
)

_LINEARIZED_WINDOW = 1024

#: Money as printed: two decimals, with or without thousands separators.
_MONEY_RE = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?!\d)")


@dataclass(frozen=True)
class HiddenText:
    page: int
    text: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class MixedToken:
    page: int
    text: str
    #: The two fonts the token is drawn in, left then right ("Helvetica 10")
    fonts: tuple[str, str]


@dataclass(frozen=True)
class AmountMismatch:
    page: int
    covered: tuple[str, ...]   # in the text layer, not seen by OCR
    pasted: tuple[str, ...]    # seen by OCR, not in the text layer


def _is_white(color: Any) -> bool:
    """Gray, RGB or CMYK fill that renders as white. Patterns/None are not."""
    if not isinstance(color, (tuple, list)) or not color:
        return False
    if len(color) == 4:  # CMYK: no ink
        return all(c <= 1 - WHITE_FILL_MIN for c in color)
    return all(c >= WHITE_FILL_MIN for c in color)


def hidden_reasons(char: dict[str, Any], width: float, height: float) -> list[str]:
    reasons = []
    if _is_white(char.get("non_stroking_color")):
        reasons.append("white text")
    if char["size"] < HIDDEN_TEXT_MAX_SIZE_PT:
        reasons.append("tiny text")
    if char["x1"] < 0 or char["x0"] > width or char["bottom"] < 0 or char["top"] > height:
        reasons.append("off-page text")
    return reasons


def _on_dark_backdrop(image: Image.Image, page: pdfplumber.page.Page, char: dict[str, Any]) -> bool:
    """Whether the rendered *image* of *page* is dark behind *char*: the
    median luminance around it is below WHITE_TEXT_BACKDROP_MAX_LUMINANCE. The
    box is widened by half the character's height on each side, so that a
    narrow glyph (an "I") does not outweigh the backdrop."""
    scale = BACKDROP_DPI / 72
    pad = (char["bottom"] - char["top"]) / 2
    left, right = char["x0"] - pad - page.bbox[0], char["x1"] + pad - page.bbox[0]
    top, bottom = char["top"] - page.bbox[1], char["bottom"] - page.bbox[1]
    box = (max(0, int(left * scale)), max(0, int(top * scale)),
           min(image.width, int(right * scale) + 1), min(image.height, int(bottom * scale) + 1))
    if box[2] <= box[0] or box[3] <= box[1]:
        return False
    pixels = sorted(image.crop(box).convert("L").tobytes())
    return pixels[len(pixels) // 2] < 255 * WHITE_TEXT_BACKDROP_MAX_LUMINANCE


def hidden_chars(page: pdfplumber.page.Page) -> list[tuple[dict[str, Any], list[str]]]:
    """Each character of *page* a reader cannot see, with why. White text
    counts only over a light backdrop, found by rendering the page (only a
    page with white text is rendered)."""
    found = [(char, hidden_reasons(char, page.width, page.height)) for char in page.chars]
    if any("white text" in why for _, why in found):
        image = page.to_image(resolution=BACKDROP_DPI).original
        for char, why in found:
            if "white text" in why and _on_dark_backdrop(image, page, char):
                why.remove("white text")
    return [(char, why) for char, why in found if why]


def hidden_text(path: Path) -> list[HiddenText]:
    """Invisible text per page, in content-stream order."""
    found: list[HiddenText] = []
    with pdfplumber.open(str(path)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            text: list[str] = []
            reasons: set[str] = set()
            for char, why in hidden_chars(page):
                text.append(char["text"])
                reasons.update(why)
            visible_chars = "".join(text).strip()
            if visible_chars:
                found.append(HiddenText(number, visible_chars, tuple(sorted(reasons))))
    return found


_SUBSET_TAG = re.compile(r"^[A-Z]{6}\+")


def font_label(word: dict[str, Any]) -> str:
    """A word's font and size as a reader would name them: "Arial-BoldMT 9.5",
    without the tag an embedded subset font carries ("ABCDEF+")."""
    return f"{_SUBSET_TAG.sub('', word['fontname'])} {round(word['size'], 1):g}"


def mixed_font_tokens(path: Path) -> list[MixedToken]:
    """Digit-bearing tokens drawn with more than one font or size."""
    found: list[MixedToken] = []
    with pdfplumber.open(str(path)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(extra_attrs=["fontname", "size"])
            for left, right in zip(words, words[1:]):
                same_line = abs(left["top"] - right["top"]) < 1
                touching = abs(right["x0"] - left["x1"]) <= TOKEN_GAP_FRACTION * left["size"]
                differs = (left["fontname"], round(left["size"], 1)) != (
                    right["fontname"],
                    round(right["size"], 1),
                )
                token = left["text"] + right["text"]
                if same_line and touching and differs and any(c.isdigit() for c in token):
                    found.append(MixedToken(number, token, (font_label(left), font_label(right))))
    return found


#: Height (points) of a band of a page: form fields in one band are one line
_LINE_BAND_PT = 4


_NUMBER_FORMAT = re.compile(r"AFNumber_Format\(\s*(\d+)")


def _inherited(widget: Any, key: str) -> Any:
    """*key* of a form field; a widget without it takes its parent field's."""
    while widget is not None:
        if key in widget:
            return widget[key]
        widget = widget["/Parent"].get_object() if "/Parent" in widget else None
    return None


def _decimals(widget: Any) -> int | None:
    """The decimal places a field's number-format script shows its value with, if it has one."""
    action = (_inherited(widget, "/AA") or {}).get("/F")
    script = action.get_object().get("/JS") if action is not None else None
    if script is None:
        return None
    script = script.get_object()
    match = _NUMBER_FORMAT.search(script.get_data().decode("latin-1") if hasattr(script, "get_data") else str(script))
    return int(match[1]) if match else None


def _field_value(widget: Any) -> str:
    """The text a form field shows: its value, a number as its format script
    writes it (the form stores 1213365 and shows 1,213,365.00)."""
    value = _inherited(widget, "/V")
    text = str(value).strip() if isinstance(value, TextStringObject) else ""
    if (decimals := _decimals(widget)) is not None and text:
        with suppress(InvalidOperation):
            text = f"{Decimal(text):.{decimals}f}"
    return text


def form_values(path: Path | str) -> list[str]:
    """Per page, the text the PDF's form fields show, in reading order (a line
    to each row of fields). A fillable form keeps its figures here and only its
    labels in the page text. A page with no fields, and every page of a PDF
    with no form, gives an empty string."""
    pages = []
    for page in pypdf.PdfReader(str(path)).pages:
        placed = []
        for ref in page.get("/Annots") or []:
            widget = ref.get_object()
            if value := _field_value(widget):
                x0, y0, x1, y1 = (float(v) for v in widget.get("/Rect", (0, 0, 0, 0)))
                placed.append((-round(max(y0, y1) / _LINE_BAND_PT), min(x0, x1), value))
        pages.append("\n".join(" ".join(v for _, _, v in row) for _, row in groupby(sorted(placed), key=lambda p: p[0])))
    return pages


def _amounts(text: str) -> dict[str, str]:
    """Normalised amount (no separators) -> amount as printed."""
    return {m.replace(",", ""): m for m in _MONEY_RE.findall(text)}


def amount_mismatches(text_pages: list[str], ocr_pages: list[str]) -> list[AmountMismatch]:
    """Per page, amounts present in only one of the text layer and OCR.

    Pages with no text layer (scans) have nothing to compare and are skipped.
    Amounts are compared as sets, so an amount OCR reads once but the page
    prints twice is not a mismatch.
    """
    found: list[AmountMismatch] = []
    for number, (text, seen) in enumerate(zip(text_pages, ocr_pages), start=1):
        if not text.strip():
            continue
        layer, ocr = _amounts(text), _amounts(seen)
        covered = tuple(layer[k] for k in sorted(layer.keys() - ocr.keys()))
        pasted = tuple(ocr[k] for k in sorted(ocr.keys() - layer.keys()))
        if covered or pasted:
            found.append(AmountMismatch(number, covered, pasted))
    return found


def incremental_updates(path: Path) -> int:
    """Revisions appended after the original save (0 for a single-save PDF).

    Counts %%EOF markers. Linearized ("fast web view") files legitimately
    carry two, so they are not counted.
    """
    raw = path.read_bytes()
    if b"/Linearized" in raw[:_LINEARIZED_WINDOW]:
        return 0
    return max(raw.count(b"%%EOF") - 1, 0)
