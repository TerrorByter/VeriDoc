"""
gldb/reference.py
-----------------
Versioned reference data: the lists a name is screened against. Two files
are read as one watchlist:

- the designations: the individuals and entities on the UN lists that MAS
  names, kept current by gldb.sanctions_sync (the UN lists are what Singapore's
  targeted financial sanctions apply);
- the bank's own watchlist: hand-kept names (in the prototype, fictional ones
  that the demo cases plant).

Company and tax records are not reference data: they are fetched per case from
ACRA and IRAS (gldb.official).

Each file carries a source, version and as-of date. Findings that use the lists
quote the versions, and the pipeline logs them, so a past case can be replayed
against the exact lists it was screened with (a designations list a sync replaces
is kept in history/). The files are read again when one changes, so a sync shows
in a running API without a restart.
"""

from __future__ import annotations

import shutil
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from gldb.config import DESIGNATIONS_PATH, WATCHLIST_PATH

#: WatchlistEntry has a field named `list`, which hides the builtin in its body
Strings = list[str]


class WatchlistEntry(BaseModel):
    name: str
    kind: Literal["entity", "individual"]
    #: The list it is on (a UN committee's list, or the bank's own)
    list: str
    reference: str
    #: MAS's category for the list: the country or regime
    category: str = ""
    listed_on: date | None = None
    #: Other names a screened name is compared with
    aliases: Strings = []
    #: Aliases the UN rates low quality: kept for the analyst, not screened
    weak_aliases: Strings = []
    nationalities: Strings = []
    birth_dates: Strings = []

    def names(self) -> Strings:
        """The names a screened name is compared with."""
        return [self.name, *self.aliases]


class ListSource(BaseModel):
    """One UN list as MAS gives it."""
    name: str
    category: str
    xml_url: str
    #: The date MAS shows for the list, as MAS writes it
    mas_updated: str


class Watchlist(BaseModel):
    source: str
    version: str
    as_of: date
    entries: list[WatchlistEntry]
    lists: list[ListSource] = []


def _read(path: Path) -> Watchlist:
    return Watchlist.model_validate_json(path.read_text(encoding="utf-8"))


def designations() -> Watchlist | None:
    """The synced UN designations; None before the first sync."""
    path = Path(DESIGNATIONS_PATH)
    return _read(path) if path.exists() else None


def replace_designations(new: Watchlist) -> None:
    """Make *new* the designations. The list it replaces goes to history/,
    named by its version; the file is swapped whole, so a reader never sees
    half of it."""
    path = Path(DESIGNATIONS_PATH)
    if (old := designations()) is not None:
        kept = path.parent / "history" / f"{path.stem}-{old.version}.json"
        kept.parent.mkdir(exist_ok=True)
        shutil.copyfile(path, kept)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".tmp")
    staged.write_text(new.model_dump_json(indent=1), encoding="utf-8")
    staged.replace(path)


@lru_cache(maxsize=1)
def _combined(files: tuple[tuple[Path, int], ...]) -> Watchlist:
    """The files as one list. *files* is each path with its modification time,
    so the cache holds only while no file has changed."""
    lists = [_read(path) for path, _ in files]
    return Watchlist(
        source=" + ".join(w.source for w in lists),
        version=" + ".join(w.version for w in lists),
        as_of=max(w.as_of for w in lists),
        entries=[e for w in lists for e in w.entries],
        lists=[s for w in lists for s in w.lists],
    )


def watchlist() -> Watchlist:
    """Every list a name is screened against: the designations, then the bank's own."""
    paths = (Path(DESIGNATIONS_PATH), Path(WATCHLIST_PATH))
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"{path} not found: run `python -m gldb.sanctions_sync` to make the designations")
    return _combined(tuple((path, path.stat().st_mtime_ns) for path in paths))


def versions() -> dict[str, str]:
    """Reference data versions, for the audit log."""
    return {"sanctions": watchlist().version}
