"""
gldb/workbook.py
----------------
Read-only reader for .xlsx workbooks, standard library only: each sheet's
cells, the sheets as text, and the file's own properties.

A workbook is untrusted data and is never run: a formula is read as the value
the file last stored, and a sheet or part larger than the limits in
gldb.config is refused.
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

from gldb.config import WORKBOOK_MAX_COLUMNS, WORKBOOK_MAX_PART_MB, WORKBOOK_MAX_ROWS

Cell = str | Decimal | None

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PACKAGE_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CELL_REF = re.compile(r"([A-Z]+)(\d+)")
#: Figures are shown to this many decimal places at most
_SHOWN_DECIMALS = 6


class WorkbookError(ValueError):
    """A file that is not a readable workbook, or is larger than the limits."""


class Sheet(NamedTuple):
    name: str
    #: Row 1 first, column A first; an empty row is [] and an empty cell None
    rows: list[list[Cell]]


class Properties(NamedTuple):
    created: datetime | None
    modified: datetime | None
    #: The program that saved the file, e.g. "Microsoft Excel"
    application: str | None


def is_workbook(path: Path | str) -> bool:
    return Path(path).suffix.lower() == ".xlsx"


def cell_text(value: Cell) -> str:
    """A cell as a reader sees it: a figure without float noise or exponent."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return f"{round(value, _SHOWN_DECIMALS).normalize():f}"


def _part(archive: zipfile.ZipFile, name: str) -> ET.Element | None:
    """An XML part of the package, None if absent. Parts that unpack larger than the limit are refused."""
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > WORKBOOK_MAX_PART_MB * 1024 * 1024:
        raise WorkbookError(f"{name} is larger than {WORKBOOK_MAX_PART_MB} MB")
    return ET.fromstring(archive.read(info))


def _open(path: Path | str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise WorkbookError(f"{Path(path).name} is not a readable workbook: {exc}") from exc


def _column_index(letters: str) -> int:
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - 64
    return index - 1


def _text(node: ET.Element) -> str:
    return "".join(t.text or "" for t in node.iter(f"{_MAIN}t"))


def _value(cell: ET.Element, shared: list[str]) -> Cell:
    kind, stored = cell.get("t"), cell.find(f"{_MAIN}v")
    if kind == "inlineStr":
        return _text(cell)
    if stored is None or stored.text is None:
        return None
    if kind == "s":
        return shared[int(stored.text)]
    if kind in ("str", "e"):
        return stored.text
    if kind == "b":
        return "TRUE" if stored.text == "1" else "FALSE"
    # Excel stores 17 digits of a binary float: its shortest repr is the figure typed
    return Decimal(repr(float(stored.text)))


def _rows(sheet: ET.Element, shared: list[str], name: str) -> list[list[Cell]]:
    cells: dict[tuple[int, int], Cell] = {}
    for cell in sheet.iter(f"{_MAIN}c"):
        ref = _CELL_REF.fullmatch(cell.get("r", ""))
        if ref is None:
            continue
        row, column = int(ref[2]) - 1, _column_index(ref[1])
        if row >= WORKBOOK_MAX_ROWS or column >= WORKBOOK_MAX_COLUMNS:
            raise WorkbookError(
                f"{name} has a cell beyond row {WORKBOOK_MAX_ROWS} or column {WORKBOOK_MAX_COLUMNS}"
            )
        cells[row, column] = _value(cell, shared)
    rows: list[list[Cell]] = [[] for _ in range(max((r for r, _ in cells), default=-1) + 1)]
    for (row, column), value in sorted(cells.items()):
        rows[row].extend([None] * (column - len(rows[row])))
        rows[row].append(value)
    return rows


def read_workbook(path: Path | str) -> list[Sheet]:
    """The sheets of the workbook at *path*, in order."""
    name = Path(path).name
    try:
        with _open(path) as archive:
            book = _part(archive, "xl/workbook.xml")
            relationships = _part(archive, "xl/_rels/workbook.xml.rels")
            if book is None or relationships is None:
                raise WorkbookError(f"{name} is not a workbook: it has no sheets")
            strings = _part(archive, "xl/sharedStrings.xml")
            shared = [_text(item) for item in strings.iter(f"{_MAIN}si")] if strings is not None else []
            targets = {rel.get("Id"): rel.get("Target", "") for rel in relationships.iter(f"{_PACKAGE_REL}Relationship")}
            sheets = []
            for sheet in book.iter(f"{_MAIN}sheet"):
                target = targets.get(sheet.get(f"{_REL}id"), "").lstrip("/")
                part = _part(archive, target if target.startswith("xl/") else f"xl/{target}")
                if part is None:
                    raise WorkbookError(f"{name} is not a workbook: sheet {sheet.get('name')!r} is missing")
                sheets.append(Sheet(sheet.get("name", ""), _rows(part, shared, name)))
    except ET.ParseError as exc:
        raise WorkbookError(f"{name} is not a readable workbook: {exc}") from exc
    return sheets


def workbook_text(path: Path | str) -> list[str]:
    """One text per sheet: a line per row that has cells, its cells joined by tabs."""
    return [
        "\n".join("\t".join(cell_text(c) for c in row if c is not None) for row in sheet.rows if any(
            c is not None for c in row
        ))
        for sheet in read_workbook(path)
    ]


def _date(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


def properties(path: Path | str) -> Properties:
    """When the file says it was created and last saved, and by which program."""
    with _open(path) as archive:
        core, app = _part(archive, "docProps/core.xml"), _part(archive, "docProps/app.xml")
    stamp = {
        which: core.findtext(f"{{http://purl.org/dc/terms/}}{which}") if core is not None else None
        for which in ("created", "modified")
    }
    application = app.findtext("{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}Application") \
        if app is not None else None
    return Properties(_date(stamp["created"]), _date(stamp["modified"]), application)
