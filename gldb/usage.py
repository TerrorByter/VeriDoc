"""
gldb/usage.py
-------------
Usage metering and cost per case ("showback").

Every billable AWS call appends one record to USAGE_LOG_PATH: Bedrock tokens
or Textract pages, with the case they were spent on. Never document content.
Metering and pricing are kept apart: records hold quantities, and prices come
from the table in gldb/config.py when a report is made, so a price change
never needs a code change and old usage can be re-priced.

Run:  python -m gldb.usage    (cost per case, most recent first)

Estimates only: the AWS bill is the record of what was charged.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from gldb.config import (
    MODEL_USD_PER_M_TOKENS,
    PRICES_AS_OF,
    TEXTRACT_USD_PER_PAGE,
    USAGE_LOG_PATH,
)

#: Case ID for records written before usage was metered per case
UNATTRIBUTED = "(unattributed)"
_MILLION = Decimal(1_000_000)


def _path() -> Path:
    return Path(os.environ.get("USAGE_LOG_PATH", "").strip() or USAGE_LOG_PATH)


def log_usage(
    service: Literal["bedrock", "textract"], purpose: str, case_id: str, **quantities: Any,
) -> None:
    """Append one usage record (quantities such as model_id, input_tokens,
    output_tokens or pages)."""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "service": service,
        "purpose": purpose,
        "case_id": case_id,
        **quantities,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def read_usage() -> list[dict[str, Any]]:
    path = _path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class CaseCost(BaseModel):
    case_id: str
    last_seen: str
    pages: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: None when any usage could not be priced (see unpriced)
    usd: Decimal | None = Decimal(0)
    unpriced: list[str] = []


def _model(model_id: str) -> str:
    """Inference profile ID from a bare ID or an ARN."""
    return model_id.rsplit("/", 1)[-1]


def report(records: list[dict[str, Any]]) -> list[CaseCost]:
    """Usage and estimated cost per case, most recently active first."""
    costs: dict[str, CaseCost] = defaultdict(lambda: CaseCost(case_id="", last_seen=""))
    for r in records:
        case_id = r.get("case_id") or UNATTRIBUTED
        row = costs[case_id]
        row.case_id = case_id
        row.last_seen = max(row.last_seen, r["ts"])
        if r.get("service") == "textract":
            row.pages += r["pages"]
            amount: Decimal | None = TEXTRACT_USD_PER_PAGE * r["pages"]
        else:  # bedrock; records before metering had no "service"
            row.input_tokens += r["input_tokens"]
            row.output_tokens += r["output_tokens"]
            model = _model(r["model_id"])
            price = MODEL_USD_PER_M_TOKENS.get(model)
            amount = (
                (price[0] * r["input_tokens"] + price[1] * r["output_tokens"]) / _MILLION
                if price else None
            )
            if price is None and model not in row.unpriced:
                row.unpriced.append(model)
        row.usd = None if row.usd is None or amount is None else row.usd + amount
    return sorted(costs.values(), key=lambda c: c.last_seen, reverse=True)


def main() -> int:
    rows = report(read_usage())
    print(f"{'case':<34}{'pages':>7}{'tokens in':>11}{'tokens out':>12}{'USD':>10}")
    for r in rows:
        usd = f"{r.usd:.4f}" if r.usd is not None else "unpriced"
        print(f"{r.case_id:<34}{r.pages:>7}{r.input_tokens:>11}{r.output_tokens:>12}{usd:>10}")
    priced = [r.usd for r in rows if r.usd is not None]
    print(f"\ntotal USD {sum(priced, Decimal(0)):.4f} (estimate; list prices as of {PRICES_AS_OF})")
    for r in rows:
        if r.unpriced:
            print(f"no price for {', '.join(r.unpriced)} ({r.case_id}): add it to MODEL_USD_PER_M_TOKENS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
