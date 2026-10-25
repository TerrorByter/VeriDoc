"""
tests/test_sanctions_sync.py
----------------------------
The sync of UN designations from MAS's page (gldb.sanctions_sync): reading
MAS's table of lists, the UN pages and XML, the database file it keeps
(gldb.reference), and the screening of names against it. MAS and the UN are
served from fixtures (tests/un_fixtures.py): no network.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import NoReturn

import httpx
import pytest

from gldb import reference, sanctions_sync, screening
from gldb.config import SANCTIONS_SOURCE_URL, SCREENING_MATCH_THRESHOLD
from gldb.reference import WatchlistEntry
from gldb.sanctions_sync import MasList, SyncError, SyncResult, mas_lists, un_entries, xml_link
from tests.un_fixtures import FIXTURES, XML_URL, UnServer

DPRK = MasList("Democratic People’s Republic of Korea", "UN 1718 List",
               "https://www.un.org/securitycouncil/sanctions/1718/materials", "17 Sep 2024")
SOUTH_SUDAN = MasList("South Sudan", "UN 2206 List", "https://www.un.org/securitycouncil/sanctions/2206/materials",
                      "25 Jun 2019")


def _entries(keyword: str, source: MasList) -> dict[str, WatchlistEntry]:
    entries, _ = un_entries((FIXTURES / "un" / f"{keyword}.xml").read_text(encoding="utf-8"), source)
    return {e.reference: e for e in entries}


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty designations file, and no waiting between attempts."""
    monkeypatch.setattr(reference, "DESIGNATIONS_PATH", str(tmp_path / "designations.json"))
    monkeypatch.setattr(sanctions_sync, "RETRY_WAIT_S", 0)
    return tmp_path


# ---------------------------------------------------------------------------
# MAS's page and the UN pages
# ---------------------------------------------------------------------------

def test_mas_page_gives_each_un_list_with_its_category_and_date() -> None:
    found = mas_lists((FIXTURES / "mas" / "lists.html").read_text(encoding="utf-8"))
    assert found == [
        DPRK, SOUTH_SUDAN,
        MasList("Sudan", "UN 1591 List", "https://www.un.org/securitycouncil/sanctions/1591/materials", "28 Apr 2026"),
        # One row, three dates: the UN lists in the order MAS gives them, and the Singapore schedule, not read here
        MasList("Counter-Terrorism", "ISIL (Da'esh) and Al-Qaida List",
                "https://www.un.org/securitycouncil/sanctions/1267/aq_sanctions_list", "15 Oct 2026"),
        MasList("Counter-Terrorism", "UN 1988 Taliban List",
                "https://www.un.org/securitycouncil/sanctions/1988/materials", "27 Aug 2026"),
    ]


@pytest.mark.parametrize("html", [
    "<html><title>Maintenance</title><p>Sorry, this service is currently unavailable.</p></html>",
    "<table><tr><th>Category</th><th>List</th></tr><tr><td>Revoked</td><td>No list</td></tr></table>",
])
def test_a_page_with_no_un_lists_is_refused(html: str) -> None:
    with pytest.raises(SyncError, match="no UN lists"):
        mas_lists(html)


def test_a_list_without_a_date_is_still_read() -> None:
    html = ('<table><tr><td>Libya</td><td>Regulations</td>'
            '<td><a href="https://www.un.org/securitycouncil/sanctions/1970/materials">UN 1970 List</a></td>'
            '<td></td></tr></table>')
    assert mas_lists(html) == [
        MasList("Libya", "UN 1970 List", "https://www.un.org/securitycouncil/sanctions/1970/materials", "")]


def test_a_un_page_gives_its_english_xml_list() -> None:
    page = "https://www.un.org/securitycouncil/sanctions/1718/materials"
    html = f'<a href="https://scsanctions.un.org/en/?keywords=dprk">Pdf</a> <a href="{XML_URL.format("dprk")}">Xml</a>'
    assert xml_link(page, html) == "https://scsanctions.un.org/xml/en/dprk"
    assert xml_link(page, f"{html} <a href='{XML_URL.format('dprk')}'>Xml</a>") == "https://scsanctions.un.org/xml/en/dprk"
    assert xml_link(page, f"<a href='{XML_URL.format('dprk')}'>Xml</a>") == "https://scsanctions.un.org/xml/en/dprk"


@pytest.mark.parametrize("html", [
    "<a href='https://scsanctions.un.org/en/?keywords=dprk'>Pdf</a>",
    f'<a href="{XML_URL.format("dprk")}">Xml</a> <a href="{XML_URL.format("iran")}">Xml</a>',
])
def test_a_un_page_without_exactly_one_xml_list_is_refused(html: str) -> None:
    with pytest.raises(SyncError, match="XML"):
        xml_link("https://www.un.org/securitycouncil/sanctions/1718/materials", html)


# ---------------------------------------------------------------------------
# The UN's XML
# ---------------------------------------------------------------------------

def test_an_individual_from_the_un_xml() -> None:
    entry = _entries("dprk", DPRK)["KPi.001"]
    assert entry == WatchlistEntry(
        name="YUN HO-JIN", kind="individual", list="UN 1718 List", category="Democratic People’s Republic of Korea",
        reference="KPi.001", listed_on=date(2009, 7, 16), aliases=["Yun Ho-chin"],
        nationalities=["Democratic People's Republic of Korea"], birth_dates=["1944-10-13"])


def test_an_entity_from_the_un_xml_keeps_every_kind_of_alias_and_skips_the_blank_ones() -> None:
    entries = _entries("dprk", DPRK)
    assert (entries["KPe.002"].kind, entries["KPe.002"].aliases) == (
        "entity", ["KOREA YONBONG GENERAL CORPORATION", "LYONGAKSAN GENERAL TRADING CORPORATION"])  # a.k.a. and f.k.a.
    assert (entries["KPe.014"].aliases, entries["KPe.014"].weak_aliases) == ([], [])  # the UN's blank placeholder
    assert entries["KPi.004"].aliases == []


def test_low_quality_aliases_are_kept_apart_and_a_repeat_of_the_name_is_dropped() -> None:
    entries = _entries("southsudan", SOUTH_SUDAN)
    dual = entries["SSi.002"]
    assert dual.name == "SIMON GATWECH DUAL" and "Simon Gatwick" in dual.aliases
    assert dual.weak_aliases == ["General Gaduel", "Dhual"] and not set(dual.weak_aliases) & set(dual.aliases)
    assert dual.listed_on == date(2015, 7, 1)  # the UN writes this one with a time zone
    assert entries["SSi.008"].aliases == ["Paul Malong", "Bol Malong"]  # "Paul Malong Awan Anei" is the name itself


def test_an_entity_named_with_its_acronym_is_also_listed_without_it() -> None:
    haqqani = _entries("taliban", MasList("Counter-Terrorism", "UN 1988 Taliban List", "", ""))["TAe.012"]
    assert (haqqani.name, haqqani.aliases) == ("HAQQANI NETWORK (HQN)", ["HAQQANI NETWORK"])


def test_birth_dates_and_nationalities_in_every_shape_the_un_uses() -> None:
    malong = _entries("southsudan", SOUTH_SUDAN)["SSi.008"]
    assert malong.nationalities == ["South Sudan", "Uganda"]
    assert malong.birth_dates == ["1962", "1960-12-04", "1960-04-12", "1962-01-01"]
    assert _entries("taliban", MasList("Counter-Terrorism", "UN 1988 Taliban List", "", ""))["TAi.002"].birth_dates == [
        "1955-1958", "1945-1950"]


def test_names_are_joined_from_their_parts_and_the_generation_date_is_read() -> None:
    entries, generated = un_entries(
        (FIXTURES / "un" / "al-qaida.xml").read_text(encoding="utf-8"), MasList("Counter-Terrorism", "AQ", "", ""))
    assert generated == date(2026, 10, 24)
    assert {e.reference: e.name for e in entries} == {"QDi.001": "MOHAMMED SALAHALDIN ABD EL HALIM ZIDANE", "QDe.004": "AL-QAIDA"}


# ---------------------------------------------------------------------------
# The sync
# ---------------------------------------------------------------------------

def test_sync_builds_the_database_from_every_list_mas_names(database: Path) -> None:
    server = UnServer()
    result = sanctions_sync.sync(server.client())
    stored = reference.designations()
    assert stored is not None
    assert [r for r in server.requests if r.startswith("https://scsanctions")] == [
        XML_URL.format(k) for k in ("dprk", "southsudan", "sudan", "al-qaida", "taliban")]
    # Sudan's XML repeats a South Sudan designation: it counts once, under the list MAS gives first
    assert len(stored.entries) == result.total == 11
    assert len({e.reference for e in stored.entries}) == 11
    assert next(e for e in stored.entries if e.reference == "SSi.002").list == "UN 2206 List"
    assert [(s.name, s.category, s.mas_updated) for s in stored.lists][:2] == [
        ("UN 1718 List", "Democratic People’s Republic of Korea", "17 Sep 2024"), ("UN 2206 List", "South Sudan", "25 Jun 2019")]
    assert stored.as_of == date(2026, 10, 24) and stored.version == result.version
    assert SANCTIONS_SOURCE_URL in stored.source
    assert (len(result.added), result.removed, result.updated) == (11, [], [])


def test_a_second_sync_of_an_unchanged_list_writes_nothing(database: Path) -> None:
    sanctions_sync.sync(UnServer().client())
    path = Path(reference.DESIGNATIONS_PATH)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    result = sanctions_sync.sync(UnServer().client())
    assert result.unchanged and (result.added, result.removed, result.updated) == ([], [], [])
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not (database / "history").exists()


def _amended(xml: str) -> str:
    """The DPRK list after the UN amends KPi.001, delists KPi.004 and adds KPe.099."""
    root = ET.fromstring(xml)
    people = root.find("INDIVIDUALS")
    assert people is not None
    for person in people.findall("INDIVIDUAL"):
        if person.findtext("REFERENCE_NUMBER") == "KPi.001":
            alias = person.find("INDIVIDUAL_ALIAS/ALIAS_NAME")
            assert alias is not None
            alias.text = "Yun Ho-jin"
        if person.findtext("REFERENCE_NUMBER") == "KPi.004":
            people.remove(person)
    entities = root.find("ENTITIES")
    assert entities is not None
    added = ET.SubElement(entities, "ENTITY")
    for tag, text in [("FIRST_NAME", "NEW TRADING CO"), ("REFERENCE_NUMBER", "KPe.099"), ("LISTED_ON", "2026-10-20")]:
        ET.SubElement(added, tag).text = text
    return ET.tostring(root, encoding="unicode")


def test_changes_are_reported_and_the_list_they_replace_is_kept(database: Path) -> None:
    first = sanctions_sync.sync(UnServer().client())
    server = UnServer()
    server.xml["dprk"] = _amended(server.xml["dprk"])

    second = sanctions_sync.sync(server.client())
    assert (second.added, second.removed, second.updated) == (["KPe.099"], ["KPi.004"], ["KPi.001"])
    assert second.version != first.version and not second.unchanged
    kept = database / "history" / f"designations-{first.version}.json"
    assert reference.Watchlist.model_validate_json(kept.read_text(encoding="utf-8")).version == first.version
    stored = reference.designations()
    assert stored is not None and stored.version == second.version


def test_a_transient_error_is_retried(database: Path) -> None:
    server = UnServer()
    answers = iter([502, 200])

    def flaky(request: httpx.Request) -> httpx.Response:
        if str(request.url) == XML_URL.format("taliban") and (status := next(answers, 200)) != 200:
            return httpx.Response(status)
        return server(request)

    assert sanctions_sync.sync(httpx.Client(transport=httpx.MockTransport(flaky))).total == 11


@pytest.mark.parametrize("break_it", [
    lambda s: s.fail.update({XML_URL.format("taliban"): 500}),
    lambda s: s.fail.update({SANCTIONS_SOURCE_URL: 503}),
    lambda s: setattr(s, "mas", "<html><title>Maintenance</title></html>"),
    lambda s: s.xml.update(taliban='<CONSOLIDATED_LIST dateGenerated="2026-10-24T23:00:07Z"><INDIVIDUALS/><ENTITIES/></CONSOLIDATED_LIST>'),
    lambda s: s.xml.update(taliban="<html>not xml"),
], ids=["list down", "MAS down", "MAS page changed", "empty list", "not XML"])
def test_a_failed_sync_leaves_the_database_as_it_was(database: Path, break_it: Callable[[UnServer], object]) -> None:
    sanctions_sync.sync(UnServer().client())
    path = Path(reference.DESIGNATIONS_PATH)
    before = path.read_bytes()
    server = UnServer()
    break_it(server)
    with pytest.raises(SyncError):
        sanctions_sync.sync(server.client())
    assert path.read_bytes() == before and not (database / "history").exists()


def test_main_runs_once_and_says_what_changed(database: Path, monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sanctions_sync, "_client", UnServer().client)
    assert sanctions_sync.main([]) == 0
    assert "11 designations" in capsys.readouterr().out
    assert sanctions_sync.main([]) == 0
    assert "unchanged" in capsys.readouterr().out

    def down() -> NoReturn:
        raise SyncError("MAS is down")

    monkeypatch.setattr(sanctions_sync, "sync", down)
    assert sanctions_sync.main([]) == 1
    assert "MAS is down" in capsys.readouterr().err


def test_main_every_n_hours_keeps_checking_after_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes: Iterator[SyncError | SyncResult] = iter([SyncError("MAS is down"), SyncResult("v1", 11, [], [], [])])
    waits: list[float] = []

    def sync_once() -> SyncResult:
        outcome = next(outcomes)
        if isinstance(outcome, SyncError):
            raise outcome
        return outcome

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(sanctions_sync, "sync", sync_once)
    monkeypatch.setattr(sanctions_sync.time, "sleep", sleep)
    assert sanctions_sync.main(["--every", "6"]) == 0
    assert waits == [6 * 3600, 6 * 3600]


# ---------------------------------------------------------------------------
# The database as the pipeline reads it
# ---------------------------------------------------------------------------

def test_the_watchlist_is_the_un_designations_and_the_banks_own_list(database: Path) -> None:
    sanctions_sync.sync(UnServer().client())
    own = reference.Watchlist.model_validate_json(Path(reference.WATCHLIST_PATH).read_text(encoding="utf-8"))
    merged = reference.watchlist()
    assert len(merged.entries) == 11 + len(own.entries)
    assert {e.list for e in merged.entries} >= {"UN 1718 List", "UN-SYN"}
    assert merged.version.endswith(own.version) and reference.versions() == {"sanctions": merged.version}


def test_a_sync_shows_without_restarting(database: Path) -> None:
    sanctions_sync.sync(UnServer().client())
    before = reference.watchlist()
    assert reference.watchlist() is before  # read once while the files stand
    server = UnServer()
    server.xml["taliban"] = server.xml["taliban"].replace("HAQQANI NETWORK (HQN)", "HAQQANI NETWORK")
    sanctions_sync.sync(server.client())
    assert "HAQQANI NETWORK" in {e.name for e in reference.watchlist().entries}


def test_screening_without_the_database_says_how_to_make_it(database: Path) -> None:
    with pytest.raises(FileNotFoundError, match="sanctions_sync"):
        reference.watchlist()


# ---------------------------------------------------------------------------
# Screening against it
# ---------------------------------------------------------------------------

def test_a_name_is_matched_to_the_listed_name_or_any_alias(database: Path) -> None:
    sanctions_sync.sync(UnServer().client())
    (hit,) = screening.screen("Ho-jin Yun")                     # word order, as ever
    assert (hit.entry.reference, hit.score, hit.matched) == ("KPi.001", 1.0, "YUN HO-JIN")
    (hit,) = screening.screen("Yun Ho-chin")                    # the UN's alias for the same person
    assert (hit.entry.reference, hit.matched) == ("KPi.001", "Yun Ho-chin")
    (hit,) = screening.screen("Al Qaeda")
    assert (hit.entry.reference, hit.matched) == ("QDe.004", "Al Qaeda")
    (hit,) = screening.screen("Lyongaksan General Trading Corp.")  # a former name
    assert hit.entry.reference == "KPe.002"


def test_an_entity_is_found_by_its_name_without_the_acronym(database: Path) -> None:
    sanctions_sync.sync(UnServer().client())
    (hit,) = screening.screen("Haqqani Network")
    assert (hit.entry.reference, hit.matched, hit.score) == ("TAe.012", "HAQQANI NETWORK", 1.0)


def test_the_uns_low_quality_aliases_are_kept_but_not_screened(database: Path) -> None:
    sanctions_sync.sync(UnServer().client())
    stored = reference.designations()
    assert stored is not None and any("Dhual" in e.weak_aliases for e in stored.entries)
    assert screening.screen("Dhual") == [] and screening.screen("Seif al Adel") == []


def test_screening_agrees_with_comparing_every_name(database: Path) -> None:
    """The length pre-filter and the cached keys change how fast, never what is found."""
    sanctions_sync.sync(UnServer().client())
    probes = ["Yun Ho Jin", "Yun Hojin", "Korea Ryonbong General Corp", "Korea Ryonbong Generl Corporation",
              "Haqqani Netwrk (HQN)", "Simon Gatwick Dual", "Simon Gatwick", "Paul Malong", "Tan Wei Ming",
              "Kestrel Marine Supplies Pte Ltd", "Amira Solenko", "Al-Qaida"]
    found = {}
    for probe in probes:
        expected = sorted(
            (entry.reference, max(screening.similarity(probe, n) for n in entry.names()))
            for entry in reference.watchlist().entries
            if max(screening.similarity(probe, n) for n in entry.names()) >= SCREENING_MATCH_THRESHOLD)
        found[probe] = sorted((m.entry.reference, m.score) for m in screening.screen(probe))
        assert found[probe] == expected, probe
    # The probes include near misses of the spelling, which is what the pre-filter could wrongly drop
    assert found["Korea Ryonbong Generl Corporation"] and found["Haqqani Netwrk (HQN)"] and found["Tan Wei Ming"] == []
    assert any(score < 1 for hits in found.values() for _, score in hits)
