"""
gldb/forensics.py
-----------------
PDF forensics primitives. Each function inspects one file and returns plain
findings; gldb.checks wraps them into Evidence for the Forensics agent.

Detects, without any AWS call:
- hidden text (white, tiny or off-page): where injected instructions and
  invisible edits live;
- mixed-font tokens: a number whose digits are drawn in different fonts or
  sizes, the trace left by overlaying edited text;
- incremental updates: a PDF saved again after creation;
- amount mismatches between the text layer and what OCR sees on the page:
  a figure covered by a box, or one pasted over the original as an image.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pdfplumber

from gldb.config import (
    HIDDEN_TEXT_MAX_SIZE_PT,
    TOKEN_GAP_FRACTION,
    WHITE_FILL_MIN,
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


def hidden_text(path: Path) -> list[HiddenText]:
    """Invisible text per page, in content-stream order."""
    found: list[HiddenText] = []
    with pdfplumber.open(str(path)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            text: list[str] = []
            reasons: set[str] = set()
            for char in page.chars:
                why = hidden_reasons(char, page.width, page.height)
                if why:
                    text.append(char["text"])
                    reasons.update(why)
            visible_chars = "".join(text).strip()
            if visible_chars:
                found.append(HiddenText(number, visible_chars, tuple(sorted(reasons))))
    return found


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
                    found.append(MixedToken(number, token))
    return found


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
