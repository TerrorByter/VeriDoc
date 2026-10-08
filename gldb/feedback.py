"""
gldb/feedback.py
----------------
Feedback report: how often analysts override each check, and why.

A projection over the append-only audit log, not a separate store, so it can
never disagree with the record. Reviewed periodically: a check dismissed most
of the time needs re-tuning, and overrides clustering on one group (for
example OCR misreads of non-English names) signal a fairness problem.

Run:  python -m gldb.feedback
"""

from __future__ import annotations

from collections import Counter, defaultdict

from pydantic import BaseModel

from gldb.audit import read_all_events
from gldb.schemas import AuditEvent


class CheckFeedback(BaseModel):
    check_id: str
    confirmed: int
    dismissed: int
    dismiss_rate: float
    #: reason_code -> count, for dismissals
    reasons: dict[str, int]


def report(events: list[AuditEvent]) -> list[CheckFeedback]:
    """Per-check flag decisions, most overridden first."""
    decisions: dict[str, Counter[str]] = defaultdict(Counter)
    reasons: dict[str, Counter[str]] = defaultdict(Counter)
    for e in events:
        if e.action not in ("flag_confirmed", "flag_dismissed"):
            continue
        check_id = e.details.get("check_id", "unknown")
        decisions[check_id][e.action] += 1
        if e.action == "flag_dismissed":
            reasons[check_id][e.reason_code or "none"] += 1
    rows = []
    for check_id, counts in decisions.items():
        confirmed, dismissed = counts["flag_confirmed"], counts["flag_dismissed"]
        rows.append(CheckFeedback(
            check_id=check_id,
            confirmed=confirmed,
            dismissed=dismissed,
            dismiss_rate=dismissed / (confirmed + dismissed),
            reasons=dict(reasons[check_id]),
        ))
    return sorted(rows, key=lambda r: (-r.dismiss_rate, r.check_id))


def main() -> None:
    rows = report(read_all_events())
    print(f"{'check':<32}{'confirmed':>10}{'dismissed':>10}{'override':>10}  reasons")
    for r in rows:
        why = ", ".join(f"{code}: {n}" for code, n in r.reasons.items()) or "-"
        print(f"{r.check_id:<32}{r.confirmed:>10}{r.dismissed:>10}{r.dismiss_rate:>10.0%}  {why}")


if __name__ == "__main__":
    main()
