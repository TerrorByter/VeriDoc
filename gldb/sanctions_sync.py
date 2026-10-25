"""
gldb/sanctions_sync.py
----------------------
Keeps the database of designated individuals and entities
(data/reference/designations.json) current.

MAS's page of designated lists names, for each country or regime (DPRK, Iran,
Libya, counter-terrorism ...), the UN Security Council list that Singapore's
targeted financial sanctions apply. Each UN page offers its list as XML. A sync
reads MAS's page, every UN page it links and every XML list, and replaces the
database when a designation was added, removed or amended.

    python -m gldb.sanctions_sync             check once
    python -m gldb.sanctions_sync --every 24  check every 24 hours until stopped

A sync fails closed: if MAS's page or any list cannot be read, or reads as
empty, the database stays as it was. A half-read sync would drop a regime's
designations without a word.

Not read: the persons only Singapore designates, in the First Schedule of the
Terrorism (Suppression of Financing) Act. That is statute text, not a list file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import NamedTuple
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx

from gldb import reference
from gldb.config import (
    SANCTIONS_SOURCE_URL,
    SANCTIONS_SYNC_EVERY_HOURS,
    SANCTIONS_TIMEOUT_S,
    SANCTIONS_USER_AGENT,
)
from gldb.reference import ListSource, Watchlist, WatchlistEntry

#: Tries at one address, and the wait between them
FETCH_ATTEMPTS = 3
RETRY_WAIT_S = 2.0

_XML_LINK = re.compile(r"""href=["']([^"']*scsanctions\.un\.org/xml/en/[^"']+)""")
_ACRONYM = re.compile(r"\([^)]*\)")
_NAME_PARTS = ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")


class SyncError(Exception):
    """The sync could not read what it needs. The database is as it was."""


class MasList(NamedTuple):
    """A UN list as MAS's table gives it."""
    category: str
    name: str
    #: The UN page that offers the list
    page: str
    #: The date MAS shows for it, as MAS writes it; empty if none
    updated: str


@dataclass(frozen=True)
class SyncResult:
    version: str
    total: int
    #: References of the designations the sync added, removed and amended
    added: list[str]
    removed: list[str]
    updated: list[str]

    @property
    def unchanged(self) -> bool:
        return not (self.added or self.removed or self.updated)


# ---------------------------------------------------------------------------
# MAS's page
# ---------------------------------------------------------------------------

class _Cell:
    """A table cell: its lines of text (a <br> or paragraph ends one) and its
    links as (text, address)."""

    def __init__(self) -> None:
        self.lines = [""]
        self.links: list[tuple[str, str]] = []

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.lines).split())

    @property
    def filled_lines(self) -> list[str]:
        return [line for line in (" ".join(raw.split()) for raw in self.lines) if line]


class _Rows(HTMLParser):
    """The cells of every table row on a page."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[_Cell]] = []
        self._cell: _Cell | None = None
        self._href: str | None = None
        self._link = ""
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th") and self.rows:
            self._cell = _Cell()
            self.rows[-1].append(self._cell)
        elif tag in ("br", "p") and self._cell:
            self._cell.lines.append("")
        elif tag == "a" and self._cell:
            self._href, self._link = dict(attrs).get("href") or "", ""

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._cell = None
        elif tag == "p" and self._cell:
            self._cell.lines.append("")
        elif tag == "a" and self._cell and self._href is not None:
            self._cell.links.append((" ".join(self._link.split()), self._href))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._cell:
            self._cell.lines[-1] += data
        if self._href is not None:
            self._link += data


def _is_un(address: str) -> bool:
    host = urlsplit(address).hostname or ""
    return host == "un.org" or host.endswith(".un.org")


def mas_lists(html: str) -> list[MasList]:
    """The UN lists in MAS's table: each row gives a category, the lists that
    apply and, in the same order, the date MAS last updated each. A row can
    also link a Singapore schedule: that is not a UN list and is skipped."""
    found = []
    for row in _Rows(html).rows:
        if len(row) < 3:
            continue
        category, lists, dates = row[0].text, row[-2], row[-1].filled_lines
        for i, (name, address) in enumerate(lists.links):
            if _is_un(address):
                found.append(MasList(category, name, address, dates[i] if i < len(dates) else ""))
    if not found:
        raise SyncError("MAS's page lists no UN lists: it may be down or its layout may have changed")
    return found


# ---------------------------------------------------------------------------
# The UN's pages and XML
# ---------------------------------------------------------------------------

def xml_link(page: str, html: str) -> str:
    """The English XML list a UN page offers. The page must offer one: a
    second would be a guess."""
    found = {urljoin(page, address) for address in _XML_LINK.findall(html)}
    if len(found) != 1:
        raise SyncError(f"{page}: expected one English XML list, found {len(found)}")
    return found.pop()


def _text(element: ElementTree.Element, tag: str) -> str:
    return " ".join((element.findtext(tag) or "").split())


def _birth_date(element: ElementTree.Element) -> str:
    """A date of birth as the UN gives it: a day, a year or a range of years."""
    if day := _text(element, "DATE"):
        return day[:10]
    if year := _text(element, "YEAR"):
        return year
    first, last = _text(element, "FROM_YEAR"), _text(element, "TO_YEAR")
    return f"{first}-{last}" if first and last else ""


def _entry(element: ElementTree.Element, source: MasList) -> WatchlistEntry:
    name = " ".join(filter(None, (_text(element, tag) for tag in _NAME_PARTS)))
    # The UN writes many entities as "NAME (ACRONYM)"; a document gives the name alone
    bare = " ".join(_ACRONYM.sub(" ", name).split())
    good: list[str] = [bare] if bare != name else []
    weak: list[str] = []
    for alias in (child for child in element if child.tag.endswith("_ALIAS")):
        alias_name = _text(alias, "ALIAS_NAME")
        if alias_name and alias_name.casefold() != name.casefold():
            (weak if _text(alias, "QUALITY") == "Low" else good).append(alias_name)
    listed_on = _text(element, "LISTED_ON")[:10]  # the UN sometimes adds a time zone
    reference_number = _text(element, "REFERENCE_NUMBER")
    if not reference_number:
        raise SyncError(f"{source.name}: a designation ('{name}') has no reference number")
    return WatchlistEntry(
        name=name,
        kind="individual" if element.tag == "INDIVIDUAL" else "entity",
        list=source.name,
        reference=reference_number,
        category=source.category,
        listed_on=date.fromisoformat(listed_on) if listed_on else None,
        aliases=list(dict.fromkeys(good)),
        weak_aliases=list(dict.fromkeys(weak)),
        nationalities=[n for v in element.findall("NATIONALITY/VALUE") if (n := " ".join((v.text or "").split()))],
        birth_dates=[b for d in element.findall("INDIVIDUAL_DATE_OF_BIRTH") if (b := _birth_date(d))],
    )


def un_entries(xml: str, source: MasList) -> tuple[list[WatchlistEntry], date]:
    """The designations of a UN XML list, and the date the UN generated it."""
    try:
        root = ElementTree.fromstring(xml)
        generated = date.fromisoformat((root.get("dateGenerated") or "")[:10])
        elements = [*root.iterfind("INDIVIDUALS/INDIVIDUAL"), *root.iterfind("ENTITIES/ENTITY")]
        return [_entry(element, source) for element in elements], generated
    except (ElementTree.ParseError, ValueError) as error:
        raise SyncError(f"{source.name}: the XML list could not be read ({error})") from error


# ---------------------------------------------------------------------------
# The sync
# ---------------------------------------------------------------------------

def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": SANCTIONS_USER_AGENT}, timeout=SANCTIONS_TIMEOUT_S,
                        follow_redirects=True)


def _get(http: httpx.Client, url: str) -> str:
    """The page at *url*, tried again after a transient error. An empty answer
    counts as an error: the UN's pages sometimes answer a busy client with one."""
    error: httpx.HTTPError | None = None
    for attempt in range(FETCH_ATTEMPTS):
        if attempt:
            time.sleep(RETRY_WAIT_S)
        try:
            response = http.get(url)
            response.raise_for_status()
            if not response.text.strip():
                raise httpx.HTTPError("empty answer")
            return response.text
        except httpx.HTTPError as caught:
            error = caught
    raise SyncError(f"{url}: {error}") from error


def sync(client: httpx.Client | None = None) -> SyncResult:
    """Read MAS's page and every list it leads to, and replace the database if
    any designation differs from it. Raises SyncError, leaving the database
    alone, if any step fails."""
    found: dict[str, WatchlistEntry] = {}
    sources: list[ListSource] = []
    generated: list[date] = []
    with (client or _client()) as http:
        for mas in mas_lists(_get(http, SANCTIONS_SOURCE_URL)):
            xml = xml_link(mas.page, _get(http, mas.page))
            entries, on = un_entries(_get(http, xml), mas)
            if not entries:
                raise SyncError(f"{mas.name}: the UN list holds no designations")
            sources.append(ListSource(name=mas.name, category=mas.category, xml_url=xml, mas_updated=mas.updated))
            generated.append(on)
            for entry in entries:  # a designation on two UN lists is kept once, under the first MAS names
                found.setdefault(entry.reference, entry)

    old = reference.designations()
    before = {e.reference: e for e in old.entries} if old else {}
    added, removed = sorted(found.keys() - before.keys()), sorted(before.keys() - found.keys())
    updated = sorted(r for r in found.keys() & before.keys() if found[r] != before[r])
    if old and not (added or removed or updated):
        return SyncResult(old.version, len(found), [], [], [])

    entries = sorted(found.values(), key=lambda e: e.reference)
    digest = hashlib.sha256(json.dumps([e.model_dump(mode="json") for e in entries], sort_keys=True).encode())
    as_of = max(generated)
    version = f"{as_of}.{digest.hexdigest()[:8]}"
    reference.replace_designations(Watchlist(
        source=f"UN Security Council sanctions lists, as listed on MAS's page {SANCTIONS_SOURCE_URL}",
        version=version, as_of=as_of, entries=entries, lists=sources,
    ))
    return SyncResult(version, len(entries), added, removed, updated)


def _summary(result: SyncResult) -> str:
    if result.unchanged:
        return f"Sanctions designations unchanged at {result.version}: {result.total} designations"
    return (f"Sanctions designations now {result.version}: {result.total} designations; "
            f"{len(result.added)} added, {len(result.removed)} removed, {len(result.updated)} amended")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update the designated individuals and entities from MAS's UN lists.")
    parser.add_argument("--every", type=float, nargs="?", const=SANCTIONS_SYNC_EVERY_HOURS, metavar="HOURS",
                        help=f"keep checking, every HOURS hours ({SANCTIONS_SYNC_EVERY_HOURS:g} if no number)")
    args = parser.parse_args(argv)
    try:
        while True:
            failed = False
            try:
                print(_summary(sync()))
            except SyncError as error:
                print(f"Sanctions sync failed; the database is as it was: {error}", file=sys.stderr)
                failed = True
            if args.every is None:
                return int(failed)
            time.sleep(args.every * 3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
