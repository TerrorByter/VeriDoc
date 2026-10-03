"""
gldb/timeline.py
----------------
The case timeline: every dated fact in the pack, in order, each traced to
its document and page, with the temporal findings about it.

A projection, like gldb.reconciliation: the facts come from
checks.dated_facts, the same function the TEMP_* checks use, so the timeline
an analyst reads and the checks always agree on the dates.
"""

from __future__ import annotations

from typing import get_args

from gldb.checks import dated_facts
from gldb.schemas import Evidence, ExtractedPack, TimelineEvent, TimelineKind

#: Same-day facts in the order a trade happens
_KIND_ORDER = {kind: i for i, kind in enumerate(get_args(TimelineKind))}


def build_timeline(pack: ExtractedPack, evidence: list[Evidence]) -> list[TimelineEvent]:
    """Dated facts in date order. A TEMP_* finding marks the fact on its
    document whose date it names (the finding's *observed* value)."""
    temporal = [e for e in evidence if e.check_id.startswith("TEMP_") and e.severity != "info"]
    events = sorted(dated_facts(pack), key=lambda f: (f.on, _KIND_ORDER[f.kind]))
    return [
        event.model_copy(update={"evidence_ids": [
            e.evidence_id for e in temporal
            if e.document == event.doc_id and e.observed == str(event.on)
        ]})
        for event in events
    ]
