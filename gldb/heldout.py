"""
gldb/heldout.py
---------------
The blind held-out test: a scenario sheet written by someone who did not
build the checks, rendered mechanically into packs, run once, and scored
against a sealed answer key.

The point of the separation is that nothing here decides what is fraudulent.
The sheet says, per pack, whether it is fraudulent or legitimate, which
typology or innocent quirk it carries, and where it sits. A typology is a
business description (an invoice overbilled, a payment made by a third
party), never a check id: this module maps it to the generator's own
faults. The author keeps the sheet and the key; only the key's SHA-256 is
published before the run.

Commands:
  python -m gldb.heldout generate --sheets SHEET.json --out DIR --key KEY.json --seed SEED
      Render the packs into DIR/packs, the official records into DIR/records,
      and the answer key into KEY.json (kept outside DIR). DIR/seal.json holds
      the key's SHA-256 and may be published.
  python -m gldb.heldout seal --key KEY.json --out SEAL.json
      Print and write the key's hash.
  python -m gldb.heldout run --packs DIR --out RESULTS.json [--ai-team] [--runs N]
      Run the pipeline over every pack and record its route. It never reads
      the key, so the run stays blind.
  python -m gldb.heldout score --results RESULTS.json --key KEY.json --seal SEAL.json --out REPORT.json
      Verify the seal, reveal the labels, and score every pack. It prints
      every pack, misses included, and refuses to overwrite an existing report
      unless --force is given.

The held-out report is what the controls page shows (HELDOUT_REPORT_PATH).
A pilot sheet (kind "pilot") is development data: it tests the renderer and
the scoring, and its numbers must never be quoted as a held-out result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ValidationError, model_validator

from gldb.config import (
    AGENT_SPECIALIST_MODEL,
    AGENT_SUPERVISOR_MODEL,
    HELDOUT_REPORT_PATH,
    OCR_ENABLED,
    OFFICIAL_RECORDS_ENABLED,
    OFFICIAL_RECORDS_URL,
    PROMPT_SCREEN_ENABLED,
    SHADOW_SCORER_MODEL,
    EXTRACTION_MODE,
    money,
)
from gldb.ai_team.team import TeamModels, flagged
from gldb.demo_packs import (
    PROFILES,
    Case,
    Company,
    Person,
    build_documents,
    write_records,
)
from gldb.demo_trade import Partner
from gldb.intake import document_paths, load_manifest
from gldb.official import OfficialRecordsError
from gldb.pipeline import assess
from gldb.schemas import ReviewPath
from gldb.stats import counted

log = logging.getLogger(__name__)

#: Optional capabilities and whether this run has them, stamped on the report
CAPABILITIES: dict[str, bool] = {"ocr": OCR_ENABLED, "prompt_screen": PROMPT_SCREEN_ENABLED}


# ---------------------------------------------------------------------------
# The sheet
# ---------------------------------------------------------------------------


class Placement(BaseModel):
    """Where a typology or quirk sits. *trade* is "sale" or "purchase" when
    the catalogue leaves the side open; *index* is 1-based among that side's
    trades, in statement order."""

    trade: Literal["sale", "purchase"] | None = None
    index: int = 1

    @model_validator(mode="after")
    def _index_from_one(self) -> "Placement":
        if self.index < 1:
            raise ValueError("placement.index is 1-based")
        return self


class CompanySpec(BaseModel):
    """The company the pack is about. The profile is the business (its
    trades, suppliers and customers); the name and UEN are the sheet's own,
    and are generated when the sheet leaves them out."""

    profile: str
    name: str | None = None
    uen: str | None = None
    scale: Decimal | None = None
    listed: bool | None = None

    @model_validator(mode="after")
    def _known_profile(self) -> "CompanySpec":
        if self.profile not in PROFILES:
            raise ValueError(f"unknown company profile {self.profile!r}; the profiles are {', '.join(PROFILES)}")
        return self


class Scenario(BaseModel):
    """One pack. The author decides the label and the typology or quirk; the
    renderer decides only how to draw it."""

    scenario_id: str
    label: Literal["fraud", "legitimate"]
    typology: str | None = None
    quirk: str | None = None
    placement: Placement | None = None
    company: CompanySpec
    notes: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> "Scenario":
        if self.label == "fraud":
            if not self.typology:
                raise ValueError("a fraudulent pack needs a typology")
            if self.quirk:
                raise ValueError("a fraudulent pack has a typology, not a quirk")
            if self.typology not in TYPOLOGIES:
                raise ValueError(f"unknown typology {self.typology!r}; the typologies are {', '.join(TYPOLOGIES)}")
            placement = TYPOLOGIES[self.typology].placement
        else:
            if not self.quirk:
                raise ValueError("a legitimate pack needs a quirk")
            if self.typology:
                raise ValueError("a legitimate pack has a quirk, not a typology")
            if self.quirk not in QUIRKS:
                raise ValueError(f"unknown quirk {self.quirk!r}; the quirks are {', '.join(QUIRKS)}")
            placement = QUIRKS[self.quirk].placement
        if placement == "pack":
            if self.placement is not None:
                raise ValueError(f"{self.typology or self.quirk} is a pack-wide choice; leave placement out")
        else:
            if self.placement is None:
                raise ValueError(f"{self.typology or self.quirk} needs a placement")
            if placement != "trade" and self.placement.trade not in (None, placement):
                raise ValueError(f"{self.typology or self.quirk} sits on a {placement}, not a {self.placement.trade}")
            if placement == "trade" and self.placement.trade is None:
                raise ValueError(f"{self.typology or self.quirk} needs placement.trade: sale or purchase")
        return self


class ScenarioSet(BaseModel):
    """The sheet. An official set is at least 40 packs with at least 20
    fraudulent and 20 legitimate; the pre-registration recommends 60, 30 of
    each. A pilot has no minimum."""

    test_set: str
    kind: Literal["pilot", "official"] = "official"
    author: str = "independent"
    scenarios: list[Scenario]

    @model_validator(mode="after")
    def _enough_to_claim(self) -> "ScenarioSet":
        ids = [s.scenario_id for s in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("scenario_id must be unique")
        if self.kind == "official":
            fraud = sum(s.label == "fraud" for s in self.scenarios)
            legitimate = len(self.scenarios) - fraud
            if len(self.scenarios) < 40:
                raise ValueError(f"an official set needs at least 40 packs, not {len(self.scenarios)}")
            if fraud < 20 or legitimate < 20:
                raise ValueError(f"an official set needs at least 20 of each label, not {fraud} and {legitimate}")
        return self


# ---------------------------------------------------------------------------
# What the sheet may say: business typologies and innocent quirks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Kind:
    """A typology or quirk the renderer can draw."""

    key: str
    name: str
    description: str
    #: "pack", or the side of the statement it sits on; "trade" lets the sheet choose
    placement: Literal["pack", "sale", "purchase", "trade"]
    build: Callable[[Case, Placement | None, str], Case]


# ---------------------------------------------------------------------------
# Synthetic names and people
# ---------------------------------------------------------------------------

_NAME_PREFIXES = (
    "ALPINE", "BOREAL", "CASCADE", "DELTA", "EMERALD", "FALCON", "GRANITE", "HARBOUR",
    "INDIGO", "JADE", "KINETIC", "LANTERN", "MERIDIAN", "NORTHERN", "ORCHID", "PINNACLE",
    "QUARRY", "RIVERSTONE", "SUMMIT", "TIDEWATER",
)
_NAME_MIDDLES = (
    "ARC", "BAY", "CREST", "FIELD", "GATE", "HILL", "LINK", "PATH",
    "POINT", "RIDGE", "STONE", "VALE", "VIEW", "WALK", "WAY", "WORKS",
)
_NAME_SUFFIXES = (
    "COMPONENTS", "ENGINEERING", "HOLDINGS", "INDUSTRIES", "LOGISTICS",
    "RESOURCES", "SUPPLIES", "SYSTEMS", "TECHNOLOGIES", "TRADING",
)
_SURNAMES = ("TAN", "LIM", "NG", "WONG", "CHUA", "GOH", "LEE", "ONG", "KOH", "TEO", "YEO", "HO", "LOW", "SIM", "CHAN")
_GIVEN_NAMES = (
    "WEI MING", "JIA HUI", "KAH SENG", "MEI LING", "BOON HUAT", "SIEW LING",
    "CHIN HONG", "YONG JIE", "HUI MIN", "KAI WEN", "SOO KENG", "PEI SHAN",
)
_POSTAL_CODES = ("068811", "238823", "109433", "387321", "556741", "629918")
_THIRD_PARTIES = (
    "GOLDEN REEF TRADING LIMITED", "PACIFIC CREST HOLDINGS LTD.",
    "ORIENT MERIDIAN TRADING PTE. LTD.", "SILVERLINE COMMODITIES LTD.",
    "BLUE ANCHOR INTERNATIONAL LIMITED",
)
_PAYMENT_AGENTS = (
    "MERIDIAN TRADE FINANCE PTE. LTD.", "STRAITS RECEIVABLES PTE. LTD.",
    "PACIFIC INVOICE FINANCE PTE. LTD.", "HARBOUR FACTORY SERVICES PTE. LTD.",
)


def _digest(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)


def _pick(text: str, options: tuple[Any, ...] | list[Any]) -> Any:
    return options[_digest(text) % len(options)]


def _company_name(seed: str) -> str:
    n = _digest(seed)
    return (f"{_NAME_PREFIXES[n % len(_NAME_PREFIXES)]} "
            f"{_NAME_MIDDLES[(n // len(_NAME_PREFIXES)) % len(_NAME_MIDDLES)]} "
            f"{_NAME_SUFFIXES[(n // (len(_NAME_PREFIXES) * len(_NAME_MIDDLES))) % len(_NAME_SUFFIXES)]} "
            "PTE. LTD.")


def _uen(seed: str) -> str:
    """A plausible, deterministic Singapore UEN. The mock register does not
    validate the format; it only needs to be unique within the set."""
    n = _digest(seed)
    year = 2005 + n % 21
    number = (n // 21) % 100000
    letter = "ABCDEFGHJKLMNPQRSTUVWXYZ"[(n // (21 * 100000)) % 24]
    return f"{year}{number:05d}{letter}"


def _fake_person(base: Person, seed: str) -> Person:
    """A person who is not the base person: a different name and NRIC, the
    same home, and no tax record (a signatory is not a guarantor)."""
    n = _digest(seed)
    name = f"{_SURNAMES[n % len(_SURNAMES)]} {_GIVEN_NAMES[(n // len(_SURNAMES)) % len(_GIVEN_NAMES)]}"
    id_number = f"S{(n // 7) % 10000000:07d}{'ABCDEFGHJKLMNPQRSTUVWXYZ'[(n // 11) % 24]}"
    return replace(base, name=name, id_number=id_number, position="DIRECTOR", tax=None)


def _different_postal(current: str, seed: str) -> str:
    choices = [code for code in _POSTAL_CODES if code != current]
    return _pick(seed, choices)


def _unique_supplier(partner: Partner, seed: str) -> Partner:
    """A pack's own copy of a supplier, with a generated name and UEN. A
    shared supplier would otherwise have one ACRA record for every pack, so a
    scenario that changes its director would change it for all of them."""
    return replace(partner, name=_company_name(f"{seed}:supplier"), uen=_uen(f"{seed}:supplier"))


def _company_from_spec(spec: CompanySpec, seed: str, name: str | None = None) -> Company:
    base = PROFILES[spec.profile]
    return replace(
        base,
        name=name or spec.name or _company_name(seed),
        uen=spec.uen or _uen(seed),
        scale=spec.scale or base.scale,
        listed=base.listed if spec.listed is None else spec.listed,
        # A profile is a clean business. Two demo companies bake in planted
        # facts (a sanctioned shareholder, a struck-off status); a held-out
        # pack must not inherit them, so the typologies plant them instead.
        shareholders=((base.chairman.name, 1000000),),
        status="LIVE COMPANY",
    )


def _trade_partner(case: Case, placement: Placement | None, side: str) -> Partner:
    if placement is None:
        raise ValueError(f"{case.name}: this choice needs a placement")
    wanted = placement.trade or side
    if side != "trade" and wanted != side:
        raise ValueError(f"{case.name}: this choice sits on a {side}, not a {wanted}")
    partners = [p for p in case.company.partners if p.supplier == (wanted == "purchase")]
    if not 1 <= placement.index <= len(partners):
        raise ValueError(f"{case.name}: {wanted} {placement.index} does not exist; there are {len(partners)}")
    return partners[placement.index - 1]


def _with_director(case: Case, partner: Partner, person: Person, seed: str) -> Case:
    """*partner* becomes the pack's own supplier, run by *person*: one ACRA
    record for this pack, not a shared one every pack would see."""
    mine = replace(_unique_supplier(partner, seed), director=(person.name, person.id_number))
    partners = tuple(mine if p.name == partner.name else p for p in case.company.partners)
    return replace(case, company=replace(case.company, partners=partners))


def _disclosure(kind: str, counterparty: str, detail: str) -> dict[str, str]:
    return {"kind": kind, "counterparty": counterparty, "detail": detail}


# ---------------------------------------------------------------------------
# Fraudulent typologies
# ---------------------------------------------------------------------------


def _invoice_overbilled(case: Case, place: Placement | None, _: str) -> Case:
    partner = _trade_partner(case, place, "trade")
    return replace(case, trade_faults=((partner.name, "overbilled"),))


def _goods_not_shipped(case: Case, place: Placement | None, _: str) -> Case:
    partner = _trade_partner(case, place, "trade")
    return replace(case, trade_faults=((partner.name, "short_shipped"),))


def _order_never_issued(case: Case, place: Placement | None, _: str) -> Case:
    partner = _trade_partner(case, place, "trade")
    return replace(case, trade_faults=((partner.name, "no_order"),))


def _financials_inflated(case: Case, _: Placement | None, __: str) -> Case:
    return replace(case, inflate_financials=Decimal(2))


def _statement_credit_raised(case: Case, place: Placement | None, _: str) -> Case:
    sale = _trade_partner(case, place, "sale")
    sales = [p for p in case.company.partners if not p.supplier]
    index = sales.index(sale) + 1
    return replace(case, tamper_credit=money(Decimal("250000") * case.company.scale),
                   tamper_credit_sale=index)


def _identity_mismatch(case: Case, _: Placement | None, seed: str) -> Case:
    fake = _fake_person(case.company.ceo, f"{seed}:signatory")
    return replace(
        case,
        signatory=fake,
        form_postal=_different_postal(case.company.office.postal, f"{seed}:postal"),
        declared_name=_fake_person(case.company.chairman, f"{seed}:name").name,
    )


def _sanctioned_shareholder(case: Case, _: Placement | None, __: str) -> Case:
    return replace(case, company=replace(case.company, shareholders=(("VIKTOR ALDANE", 1000000),)))


def _company_struck_off(case: Case, _: Placement | None, __: str) -> Case:
    return replace(case, company=replace(case.company, status="STRUCK OFF"))


def _third_party_payment(case: Case, place: Placement | None, seed: str) -> Case:
    buyer = _trade_partner(case, place, "sale")
    return replace(case, receipts_name_payers=True,
                   paid_by=((buyer.name, _pick(seed, _THIRD_PARTIES)),))


def _related_party_undisclosed(case: Case, place: Placement | None, seed: str) -> Case:
    supplier = _trade_partner(case, place, "purchase")
    return _with_director(case, supplier, case.company.ceo, seed)


def _round_trip(case: Case, place: Placement | None, _: str) -> Case:
    supplier = _trade_partner(case, place, "purchase")
    return replace(case, round_trip=supplier.name)


# ---------------------------------------------------------------------------
# Innocent look-alikes
# ---------------------------------------------------------------------------


def _clean(case: Case, _: Placement | None, __: str) -> Case:
    return case


def _supplier_refund(case: Case, place: Placement | None, _: str) -> Case:
    supplier = _trade_partner(case, place, "purchase")
    return replace(case, refund=(supplier.name, money(Decimal("7500") * case.company.scale), 18))


def _agent_payment(case: Case, place: Placement | None, seed: str) -> Case:
    buyer = _trade_partner(case, place, "sale")
    agent = _pick(seed, _PAYMENT_AGENTS)
    return replace(
        case,
        receipts_name_payers=True,
        paid_by=((buyer.name, f"{agent} FOR {buyer.legal_name}"),),
        disclosures=(_disclosure(
            "payment_agent", agent,
            "The buyer's payment agent settled this invoice on the buyer's behalf."),),
    )


def _sister_company(company: Company) -> str:
    return f"{company.name.split()[0]} GROUP HOLDINGS PTE. LTD."


def _intra_group_payer(case: Case, place: Placement | None, _: str) -> Case:
    buyer = _trade_partner(case, place, "sale")
    sister = _sister_company(case.company)
    return replace(
        case,
        receipts_name_payers=True,
        paid_by=((buyer.name, f"{sister} FOR {buyer.legal_name}"),),
        disclosures=(_disclosure(
            "intra_group_payment", sister,
            "A company in the applicant's group settled the buyer's invoice on its behalf."),),
    )


def _disclosed_directorship(case: Case, place: Placement | None, seed: str) -> Case:
    supplier = _trade_partner(case, place, "purchase")
    mine = _unique_supplier(supplier, seed)
    return replace(
        _with_director(case, supplier, case.company.ceo, seed),
        disclosures=(_disclosure(
            "directorship", mine.legal_name,
            f"{case.company.ceo.name} is a director of this supplier; the interest is disclosed."),),
    )


def _split_payment(case: Case, place: Placement | None, _: str) -> Case:
    buyer = _trade_partner(case, place, "sale")
    return replace(case, receipts_name_payers=True, paid_in_parts=(buyer.name,))


TYPOLOGIES: dict[str, Kind] = {
    "invoice_overbilled": Kind(
        "invoice_overbilled", "Invoice overbilled",
        "An invoice charges more than its purchase order and the payment for the same goods.",
        "trade", _invoice_overbilled),
    "goods_not_shipped": Kind(
        "goods_not_shipped", "Goods billed but not shipped",
        "An invoice bills goods that the bill of lading or delivery order does not carry.",
        "trade", _goods_not_shipped),
    "order_never_issued": Kind(
        "order_never_issued", "Order never issued",
        "An invoice names a purchase order that is not in the pack.",
        "trade", _order_never_issued),
    "financials_inflated": Kind(
        "financials_inflated", "Financials inflated",
        "The submitted financial statements overstate what was filed with ACRA.",
        "pack", _financials_inflated),
    "statement_credit_raised": Kind(
        "statement_credit_raised", "Statement credit raised",
        "A sales credit on the bank statement was raised and the PDF re-saved.",
        "sale", _statement_credit_raised),
    "identity_mismatch": Kind(
        "identity_mismatch", "Identity mismatch",
        "The forms disagree with the registry on the signatory, the office or a person's name.",
        "pack", _identity_mismatch),
    "sanctioned_shareholder": Kind(
        "sanctioned_shareholder", "Sanctioned shareholder",
        "A shareholder is on the sanctions list.",
        "pack", _sanctioned_shareholder),
    "company_struck_off": Kind(
        "company_struck_off", "Company struck off",
        "The company is struck off in the registry.",
        "pack", _company_struck_off),
    "third_party_payment": Kind(
        "third_party_payment", "Third-party payment",
        "A sale is paid by a company with no part in it.",
        "sale", _third_party_payment),
    "related_party_undisclosed": Kind(
        "related_party_undisclosed", "Undisclosed related party",
        "A supplier is run by one of the applicant's own officers and nothing declares it.",
        "purchase", _related_party_undisclosed),
    "round_trip": Kind(
        "round_trip", "Round trip",
        "A supplier payment comes back from the supplier as a customer receipt.",
        "purchase", _round_trip),
}

QUIRKS: dict[str, Kind] = {
    "clean": Kind(
        "clean", "Plainly clean",
        "Nothing: the documents agree with each other and with the registry.",
        "pack", _clean),
    "supplier_refund": Kind(
        "supplier_refund", "Supplier refund",
        "A supplier returns money for goods returned; the credit has no invoice.",
        "purchase", _supplier_refund),
    "agent_payment": Kind(
        "agent_payment", "Payment agent",
        "A payment agent or factoring company settles the buyer's invoice on its behalf.",
        "sale", _agent_payment),
    "intra_group_payer": Kind(
        "intra_group_payer", "Intra-group payer",
        "A company in the same group settles the buyer's invoice on its behalf.",
        "sale", _intra_group_payer),
    "disclosed_directorship": Kind(
        "disclosed_directorship", "Disclosed directorship",
        "A supplier's director is also one of the applicant's officers, and the application declares it.",
        "purchase", _disclosed_directorship),
    "split_payment": Kind(
        "split_payment", "Invoice paid in parts",
        "The buyer pays the invoice in two instalments.",
        "sale", _split_payment),
}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _pack_id(seed: str, scenario_id: str, index: int) -> str:
    digest = hashlib.sha256(f"{seed}:{scenario_id}:{index}".encode("utf-8")).hexdigest()
    return f"P{digest[:10].upper()}"


def build_case(scenario: Scenario, pack_id: str) -> Case:
    """The Case the renderer draws, from the sheet alone. The expected fields
    are placeholders: a held-out pack has no ground truth in it."""
    company = _company_from_spec(scenario.company, pack_id)
    case = Case(
        name=pack_id, company=company, note="",
        expected_band="Low", expected_path="fast_track", expected_readiness="Complete",
        application_id=pack_id,
    )
    builder = TYPOLOGIES[scenario.typology].build if scenario.label == "fraud" else QUIRKS[scenario.quirk].build
    return builder(case, scenario.placement, pack_id)


def _empty_mock(records: Path) -> None:
    records.mkdir(parents=True, exist_ok=True)
    for name, key in (("acra.json", "companies"), ("iras.json", "notices")):
        path = records / name
        if not path.exists():
            path.write_text(json.dumps({
                "source": "Mock ACRA BizFile register (held-out test set)",
                "version": "HELDOUT-1",
                "as_of": date.today().isoformat(),
                key: [],
            }, indent=2) + "\n", encoding="utf-8")


def generate(sheets: Path, out: Path, key_path: Path, seed: str, records: Path | None = None) -> Path:
    """Render the sheet into packs, records and a sealed key. The key is kept
    outside *out*; *out*/seal.json is safe to publish."""
    data = ScenarioSet.model_validate_json(sheets.read_text(encoding="utf-8"))
    out = Path(out)
    key_path = Path(key_path)
    if out == key_path or out in key_path.parents:
        raise ValueError("keep the answer key outside the generated folder")
    packs = out / "packs"
    shutil.rmtree(packs, ignore_errors=True)
    packs.mkdir(parents=True)
    records = Path(records) if records else out / "records"
    _empty_mock(records)

    cases: list[Case] = []
    key_packs: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, scenario in enumerate(data.scenarios):
        pack_id = _pack_id(seed, scenario.scenario_id, index)
        case = build_case(scenario, pack_id)
        if case.company.name in names:
            raise ValueError(f"{scenario.scenario_id}: the generated company name {case.company.name!r} is already used")
        names.add(case.company.name)
        build_documents(case, packs / pack_id)
        cases.append(case)
        key_packs.append({
            "pack": pack_id,
            "scenario_id": scenario.scenario_id,
            "label": scenario.label,
            "typology": scenario.typology,
            "quirk": scenario.quirk,
            "placement": scenario.placement.model_dump() if scenario.placement else None,
            "company": case.company.name,
            "uen": case.company.uen,
        })

    write_records(records, cases)
    key = {"test_set": data.test_set, "kind": data.kind, "author": data.author, "packs": key_packs}
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")
    seal = {
        "test_set": data.test_set,
        "kind": data.kind,
        "author": data.author,
        "packs": len(key_packs),
        "sha256": key_hash(key),
    }
    (out / "seal.json").write_text(json.dumps(seal, indent=2) + "\n", encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Seal and reveal
# ---------------------------------------------------------------------------


def canonical_key(key: dict[str, Any]) -> bytes:
    """The key's canonical bytes: sorted keys, no insignificant whitespace."""
    return json.dumps(key, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def key_hash(key: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_key(key)).hexdigest()


def read_seal(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_seal(key: dict[str, Any], seal: dict[str, Any]) -> None:
    """Raise ValueError unless *seal* is the hash of *key*."""
    if seal.get("sha256") != key_hash(key):
        raise ValueError("the answer key does not match the published seal")
    if seal.get("test_set") != key.get("test_set"):
        raise ValueError("the seal names a different test set")


# ---------------------------------------------------------------------------
# Run (blind: the key is not read)
# ---------------------------------------------------------------------------


class HeldoutPackRun(BaseModel):
    pack: str
    run: int = 1
    band: str
    readiness: str
    #: The rules' route; "-" when the pipeline failed
    path: str
    qa_sampled: bool = False
    risk_findings: int = 0
    #: "agent:pattern" leads code verified
    verified_leads: list[str] = []
    #: The route the log-only gate would take with the AI team on; None without it
    gate_route: ReviewPath | None = None
    shadow_route: ReviewPath | None = None
    error: str | None = None


class HeldoutRun(BaseModel):
    evaluated_at: str
    extraction_mode: str
    capabilities: dict[str, bool]
    runs: int = 1
    packs: list[HeldoutPackRun]


def _preflight(folders: list[Path]) -> None:
    """Refuse to run if the official records the packs need are not served.
    Without them every pack is incomplete and the false-alarm measure means
    nothing."""
    if not OFFICIAL_RECORDS_ENABLED:
        return
    from gldb import official

    for folder in folders:
        manifest = load_manifest(folder)
        if manifest is None:
            raise SystemExit(f"{folder.name}: no application.json")
        try:
            if official.company(manifest.uen) is None:
                raise SystemExit(
                    f"{folder.name}: ACRA has no record for {manifest.uen}. Start mock_gov with "
                    f"MOCK_GOV_DATA_DIR pointing at the held-out records, and set OFFICIAL_RECORDS_URL "
                    f"(now {OFFICIAL_RECORDS_URL}).")
            missing = [g.name for g in manifest.guarantors if official.notice(g.id_number) is None]
        except OfficialRecordsError as exc:
            raise SystemExit(f"{folder.name}: {exc}") from exc
        if missing:
            raise SystemExit(f"{folder.name}: IRAS has no notice for {', '.join(missing)}; the pack would be incomplete")


def _assess_pack(folder: Path, team: Any, run: int) -> HeldoutPackRun:
    try:
        result = assess(folder.name, document_paths(folder), load_manifest(folder), team=team)
    except Exception as exc:  # noqa: BLE001 - report any pipeline failure per pack
        return HeldoutPackRun(pack=folder.name, run=run, band="-", readiness="-", path="-",
                              error=f"{type(exc).__name__}: {exc}")
    leads = result.ai_team.leads if result.ai_team else []
    verified = sorted({f"{lead.agent}:{lead.pattern}" for lead in leads
                       if lead.outcome == "kept" and lead.kind == "pattern" and flagged(lead)})
    return HeldoutPackRun(
        pack=folder.name, run=run,
        band=result.risk.band,
        readiness=result.readiness.status if result.readiness else "-",
        path=result.routing.path if result.routing else "-",
        qa_sampled=bool(result.routing and result.routing.qa_sampled),
        risk_findings=sum(e.category == "fraud_signal" and e.severity != "info" for e in result.evidence),
        verified_leads=verified,
        gate_route=result.gate.would_route if result.gate else None,
        shadow_route=result.shadow.route if result.shadow else None,
    )


def run(packs_dir: Path, results_path: Path, ai_team: bool = False, runs: int = 1) -> HeldoutRun:
    """Run every pack in *packs_dir* and record its route. The answer key is
    never read here."""
    folders = sorted(p for p in Path(packs_dir).iterdir() if (p / "application.json").is_file())
    if not folders:
        raise SystemExit(f"no packs in {packs_dir}")
    _preflight(folders)
    team = (TeamModels(AGENT_SUPERVISOR_MODEL, AGENT_SPECIALIST_MODEL, SHADOW_SCORER_MODEL)
            if ai_team else None)
    runs = max(1, runs) if ai_team else 1
    results = [_assess_pack(folder, team, run_no)
               for run_no in range(1, runs + 1)
               for folder in folders]
    report = HeldoutRun(
        evaluated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        extraction_mode=EXTRACTION_MODE,
        capabilities=CAPABILITIES,
        runs=runs,
        packs=results,
    )
    Path(results_path).parent.mkdir(parents=True, exist_ok=True)
    Path(results_path).write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# Score (after the key is revealed)
# ---------------------------------------------------------------------------


class HeldoutPackResult(BaseModel):
    pack: str
    scenario_id: str
    label: Literal["fraud", "legitimate"]
    typology: str | None = None
    quirk: str | None = None
    placement: dict[str, Any] | None = None
    company: str
    uen: str
    band: str
    readiness: str
    path: str
    qa_sampled: bool = False
    risk_findings: int = 0
    verified_leads: list[str] = []
    gate_route: ReviewPath | None = None
    #: Runs the gate would send for review, and the runs it was asked about
    gate_review_runs: int = 0
    gate_runs: int = 0
    #: Runs with at least one code-verified lead
    verified_lead_runs: int = 0
    error: str | None = None
    outcome: Literal["caught", "missed", "clear", "false_alarm", "error"]


class HeldoutReport(BaseModel):
    test_set: str
    kind: Literal["pilot", "official"]
    author: str
    seal: str
    scored_at: str
    evaluated_at: str
    extraction_mode: str
    capabilities: dict[str, bool]
    runs: int = 1
    fraud_packs: int
    legitimate_packs: int
    caught: int
    missed: int
    clear: int
    false_alarms: int
    errors: int
    #: Caught / fraudulent packs, and false alarms / legitimate packs
    recall: float
    false_alarm_rate: float
    #: Secondary: with the AI team, the gate's route over every run
    gate_review_runs: int
    gate_runs: int
    verified_lead_runs: int
    packs: list[HeldoutPackResult]


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def score(results_path: Path, key_path: Path, seal_path: Path, report_path: Path,
          force: bool = False) -> HeldoutReport:
    """Verify the seal, join the revealed key to the run, and score every
    pack. Refuses to overwrite an existing report unless *force*."""
    report_path = Path(report_path)
    if report_path.exists() and not force:
        raise SystemExit(
            f"{report_path} exists. A pre-registered set runs once; use --force only for a pilot or a dry run.")
    key = json.loads(Path(key_path).read_text(encoding="utf-8"))
    seal = read_seal(seal_path)
    verify_seal(key, seal)
    run = HeldoutRun.model_validate_json(Path(results_path).read_text(encoding="utf-8"))

    labelled = {entry["pack"]: entry for entry in key["packs"]}
    ran = {entry.pack for entry in run.packs}
    if ran != set(labelled):
        missing = sorted(set(labelled) - ran)
        extra = sorted(ran - set(labelled))
        raise SystemExit(f"the run and the key name different packs; missing {missing}, unexpected {extra}")

    results: list[HeldoutPackResult] = []
    for pack_id, entry in labelled.items():
        entries = [p for p in run.packs if p.pack == pack_id]
        paths = {p.path for p in entries}
        if len(paths) > 1:
            raise SystemExit(f"{pack_id}: the rules' route changed between runs: {sorted(paths)}")
        first = entries[0]
        label = entry["label"]
        if first.error:
            outcome = "error"
        elif label == "fraud":
            outcome = "caught" if first.path != "fast_track" else "missed"
        else:
            outcome = "clear" if first.path == "fast_track" else "false_alarm"
        results.append(HeldoutPackResult(
            pack=pack_id,
            scenario_id=entry["scenario_id"],
            label=label,
            typology=entry.get("typology"),
            quirk=entry.get("quirk"),
            placement=entry.get("placement"),
            company=entry["company"],
            uen=entry["uen"],
            band=first.band,
            readiness=first.readiness,
            path=first.path,
            qa_sampled=first.qa_sampled,
            risk_findings=first.risk_findings,
            verified_leads=sorted({lead for p in entries for lead in p.verified_leads}),
            gate_route=first.gate_route,
            gate_review_runs=sum(p.gate_route not in (None, "fast_track") for p in entries),
            gate_runs=sum(p.gate_route is not None for p in entries),
            verified_lead_runs=sum(bool(p.verified_leads) for p in entries),
            error=first.error,
            outcome=outcome,
        ))

    fraud = [p for p in results if p.label == "fraud"]
    legitimate = [p for p in results if p.label == "legitimate"]
    caught = sum(p.outcome == "caught" for p in fraud)
    missed = sum(p.outcome == "missed" for p in fraud)
    clear = sum(p.outcome == "clear" for p in legitimate)
    false_alarms = sum(p.outcome == "false_alarm" for p in legitimate)
    errors = sum(p.outcome == "error" for p in results)
    report = HeldoutReport(
        test_set=key["test_set"],
        kind=key["kind"],
        author=key.get("author", "independent"),
        seal=seal["sha256"],
        scored_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        evaluated_at=run.evaluated_at,
        extraction_mode=run.extraction_mode,
        capabilities=run.capabilities,
        runs=run.runs,
        fraud_packs=len(fraud),
        legitimate_packs=len(legitimate),
        caught=caught,
        missed=missed,
        clear=clear,
        false_alarms=false_alarms,
        errors=errors,
        recall=_ratio(caught, len(fraud)),
        false_alarm_rate=_ratio(false_alarms, len(legitimate)),
        gate_review_runs=sum(p.gate_review_runs for p in results),
        gate_runs=sum(p.gate_runs for p in results),
        verified_lead_runs=sum(p.verified_lead_runs for p in results),
        packs=results,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def load_saved() -> HeldoutReport | None:
    """The saved held-out report the controls page shows, if any."""
    path = Path(os.environ.get("HELDOUT_REPORT_PATH", HELDOUT_REPORT_PATH))
    if not path.is_file():
        return None
    try:
        return HeldoutReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, ValueError) as exc:
        log.warning("ignoring unreadable held-out report %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _print_run(report: HeldoutRun) -> None:
    width = max([len("pack"), *(len(p.pack) for p in report.packs)]) + 2
    print(f"{'pack':<{width}}{'run':<5}{'band':<10}{'readiness':<14}{'route':<20}{'QA':<6}risk findings")
    for p in report.packs:
        if p.error:
            print(f"{p.pack:<{width}}{p.run:<5}ERROR {p.error}")
            continue
        print(f"{p.pack:<{width}}{p.run:<5}{p.band:<10}{p.readiness:<14}{p.path:<20}"
              f"{'yes' if p.qa_sampled else '-':<6}{p.risk_findings}")
    print(f"\n{len(report.packs)} pack runs. The answer key was not read.")


def _label(kind: str | None, quirk: str | None) -> str:
    return kind or quirk or "-"


def _print_report(report: HeldoutReport) -> None:
    width = max([len("pack"), *(len(p.pack) for p in report.packs)]) + 2
    print(f"{report.test_set} ({report.kind})  seal {report.seal[:12]}...  {report.fraud_packs} fraudulent, "
          f"{report.legitimate_packs} legitimate")
    print(f"{'pack':<{width}}{'label':<12}{'typology / quirk':<32}{'route':<20}outcome")
    for p in report.packs:
        print(f"{p.pack:<{width}}{p.label:<12}{_label(p.typology, p.quirk):<32}{p.path:<20}{p.outcome}")
    print(
        f"\nfraudulent packs caught {counted(report.caught, report.fraud_packs)}\n"
        f"legitimate packs sent for review {counted(report.false_alarms, report.legitimate_packs)}\n"
        f"errors {report.errors}"
    )
    if report.gate_runs:
        print(f"with the AI team, the gate would send {report.gate_review_runs} of {report.gate_runs} runs for review; "
              f"{report.verified_lead_runs} runs carried a verified lead")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gldb.heldout",
        description="Render, run and score the blind held-out test set.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    generate_parser = sub.add_parser("generate", help="render the packs, records and sealed key from a sheet")
    generate_parser.add_argument("--sheets", required=True, type=Path)
    generate_parser.add_argument("--out", required=True, type=Path)
    generate_parser.add_argument("--key", required=True, type=Path)
    generate_parser.add_argument("--seed", required=True)
    generate_parser.add_argument("--records", type=Path, default=None)

    seal_parser = sub.add_parser("seal", help="hash the answer key")
    seal_parser.add_argument("--key", required=True, type=Path)
    seal_parser.add_argument("--out", required=True, type=Path)

    run_parser = sub.add_parser("run", help="run the pipeline over the packs, without the key")
    run_parser.add_argument("--packs", required=True, type=Path)
    run_parser.add_argument("--out", required=True, type=Path)
    run_parser.add_argument("--ai-team", action="store_true")
    run_parser.add_argument("--runs", type=int, default=1, choices=range(1, 4))

    score_parser = sub.add_parser("score", help="verify the seal and score the run against the key")
    score_parser.add_argument("--results", required=True, type=Path)
    score_parser.add_argument("--key", required=True, type=Path)
    score_parser.add_argument("--seal", required=True, type=Path)
    score_parser.add_argument("--out", required=True, type=Path)
    score_parser.add_argument("--force", action="store_true", help="overwrite an existing report (pilot or dry run)")

    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            out = generate(args.sheets, args.out, args.key, args.seed, args.records)
            data = ScenarioSet.model_validate_json(args.sheets.read_text(encoding="utf-8"))
            seal = read_seal(out / "seal.json")
            print(f"Wrote {len(data.scenarios)} packs to {out / 'packs'}, records to "
                  f"{args.records or out / 'records'}, and the key to {args.key}")
            print(f"seal {seal['sha256']}  ({seal['test_set']}, {seal['packs']} packs)")
            print("Keep the key. Publish the seal and the packs; never the key, before the run.")
            return 0
        if args.command == "seal":
            key = json.loads(args.key.read_text(encoding="utf-8"))
            seal = {"test_set": key["test_set"], "kind": key["kind"], "author": key.get("author", "independent"),
                    "packs": len(key["packs"]), "sha256": key_hash(key)}
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(seal, indent=2) + "\n", encoding="utf-8")
            print(f"seal {seal['sha256']}  ({seal['test_set']}, {seal['packs']} packs)")
            return 0
        if args.command == "run":
            report = run(args.packs, args.out, args.ai_team, args.runs)
            _print_run(report)
            print(f"Saved to {args.out}")
            return 0
        report = score(args.results, args.key, args.seal, args.out, args.force)
        _print_report(report)
        print(f"\nSaved to {args.out}")
        return 0
    except (ValueError, ValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
