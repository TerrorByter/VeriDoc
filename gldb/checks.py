"""
gldb/checks.py
--------------
All deterministic fraud, compliance, and completeness checks.

Rules:
- Each check is a pure function check_x(pack: ExtractedPack) -> list[Evidence].
- method = "deterministic" on every Evidence.
- category set explicitly on every Evidence.
- Decimal arithmetic only; use gldb.config.money() for all monetary values.
- Mask personal identifiers with gldb.privacy.mask_id(). UENs are not masked.
- If pack.manifest is None, skip manifest-dependent checks and emit
  MANIFEST_MISSING (completeness, medium, 1.0).
- Checks are grouped into rule groups (section E); run_rule_checks() runs them.
- FORM_DOC_TYPES docs are skipped for META_MOD_AFTER_CREATE and
  META_PRODUCER_MISMATCH.
"""

from __future__ import annotations

import re
import string
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from time import perf_counter
from typing import Callable

from gldb.config import (
    ACRA_COMMON_FACTOR_MIN_FIGURES,
    ACRA_FIGURES_UNIT,
    ACRA_UNITS_FACTORS,
    BANK_DESCRIPTION_WORDS,
    BANK_RECENCY_DAYS,
    BANK_REVENUE_MIN_DAYS,
    BANK_REVENUE_RATIO_MAX,
    BANK_REVENUE_RATIO_MIN,
    CHECKLIST,
    DEBT_INFLOW_WORDS,
    DEBT_INTEREST_WORDS,
    DEBT_NAME_WORDS,
    DEBT_REPAYMENT_WORDS,
    GST_RATE,
    HIDDEN_QUOTE_MAX_CHARS,
    MATCH_WINDOW_DAYS,
    META_MOD_GAP_HOURS,
    NON_SALES_CREDIT_WORDS,
    PASS_THROUGH_DAYS,
    PASS_THROUGH_MIN_SHARE,
    PASS_THROUGH_MIN_VOLUME_SHARE,
    REQUIRED_BANK_MONTHS,
    SCREENING_MATCH_THRESHOLD,
    START_UP_MAX_MONTHS,
    TOLERANCE_SGD,
    money,
)
from gldb.forensics import (
    amount_mismatches,
    hidden_text,
    incremental_updates,
    mixed_font_tokens,
)
from gldb.intake import FORM_DOC_TYPES, model_view
from gldb.ordering import TradeDates, breaks, violations
from gldb.privacy import mask_id
from gldb.reference import HighRiskCountry, high_risk_countries, watchlist
from gldb.schemas import (
    AcraFinancials,
    AcraProfile,
    ApplicationManifest,
    BankStatement,
    DocMeta,
    EntitySelfCert,
    Evidence,
    EvidenceCategory,
    EvidenceRow,
    ExtractedPack,
    FinancialStatementSummary,
    Guarantor,
    IRAS_DOC,
    Invoice,
    NoticeOfAssessment,
    PurchaseOrder,
    RuleGroupReport,
    Severity,
    Shipment,
    TimelineEvent,
    Transaction,
)
from gldb.screening import birth_date_fits, high_risk_country, screen, screening_key, similarity
from gldb.workbook import is_workbook

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


PACK_DOC = "pack"   # for cross-document evidence with no single source


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _ev(
    check_id: str,
    category: EvidenceCategory,
    severity: Severity,
    document: str,
    page: int,
    explanation: str,
    *,
    quote: str | None = None,
    expected: str | None = None,
    observed: str | None = None,
) -> Evidence:
    return Evidence(
        evidence_id="",   # assigned later by run_rule_checks
        check_id=check_id,
        category=category,
        severity=severity,
        document=document,
        page=page,
        quote=quote,
        expected=expected,
        observed=observed,
        method="deterministic",
        explanation=explanation,
    )


def normalise_name(s: str) -> str:
    """Uppercase, remove punctuation, strip trailing entity suffixes, collapse spaces."""
    s = s.upper()
    # Remove all punctuation
    s = s.translate(str.maketrans("", "", string.punctuation))
    # Collapse whitespace first so suffix matching is reliable
    s = " ".join(s.split())
    # Strip trailing entity suffixes (longest first)
    for suffix in (
        "PRIVATE LIMITED",
        "PTE LTD",
        "PTE",
        "LTD",
    ):
        if s == suffix:
            s = ""
            break
        if s.endswith(" " + suffix):
            s = s[: -(len(suffix) + 1)]
            break
    # Final collapse after suffix removal
    return " ".join(s.split())


def _manifest_missing_evidence(document: str = PACK_DOC) -> Evidence:
    return _ev(
        "MANIFEST_MISSING",
        "completeness",
        "medium",
        document,
        1,
        "application.json is missing; manifest-dependent checks were skipped.",
    )


# ---------------------------------------------------------------------------
# A. FRAUD SIGNAL CHECKS
# ---------------------------------------------------------------------------

# 1. INV_ARITH -----------------------------------------------------------

def check_inv_arith(pack: ExtractedPack) -> list[Evidence]:
    """Each line amount == qty × unit_price; subtotal/gst/total arithmetic."""
    results: list[Evidence] = []
    for inv in pack.invoices:
        doc = inv.doc_id

        # a) per-line: amount == qty × unit_price
        for item in inv.line_items:
            expected_amt = money(item.quantity * item.unit_price)
            if abs(expected_amt - item.amount) > TOLERANCE_SGD:
                results.append(_ev(
                    "INV_ARITH", "fraud_signal", "high", doc, inv.page,
                    f"Line item '{item.description}': amount SGD {item.amount} "
                    f"does not equal qty {item.quantity} × unit price "
                    f"SGD {item.unit_price} = SGD {expected_amt}.",
                    expected=f"SGD {expected_amt}",
                    observed=f"SGD {item.amount}",
                    quote=item.description,
                ))

        # b) subtotal == sum of line amounts
        expected_subtotal = money(sum(i.amount for i in inv.line_items))
        if abs(expected_subtotal - inv.subtotal) > TOLERANCE_SGD:
            results.append(_ev(
                "INV_ARITH", "fraud_signal", "high", doc, inv.page,
                f"Invoice subtotal SGD {inv.subtotal} does not equal sum of "
                f"line amounts SGD {expected_subtotal}.",
                expected=f"SGD {expected_subtotal}",
                observed=f"SGD {inv.subtotal}",
                quote=inv.source_quotes.get("Subtotal"),
            ))

        # c) the rate is Singapore's, and gst == subtotal × that rate
        if inv.gst_rate not in (GST_RATE, 0):
            results.append(_ev(
                "INV_ARITH", "fraud_signal", "high", doc, inv.page,
                f"The invoice charges GST at {inv.gst_rate:.0%}, a rate Singapore does not charge: "
                f"{GST_RATE:.0%}, or 0% on a zero-rated supply such as an export.",
                expected=f"{GST_RATE:.0%} or, for a zero-rated supply, 0%",
                observed=f"{inv.gst_rate:.0%}",
                quote=inv.source_quotes.get("GST"),
            ))
        expected_gst = money(inv.subtotal * inv.gst_rate)
        if abs(expected_gst - inv.gst) > TOLERANCE_SGD:
            results.append(_ev(
                "INV_ARITH", "fraud_signal", "high", doc, inv.page,
                f"GST SGD {inv.gst} does not equal subtotal SGD {inv.subtotal} "
                f"× {inv.gst_rate:.0%} = SGD {expected_gst}.",
                expected=f"SGD {expected_gst}",
                observed=f"SGD {inv.gst}",
                quote=inv.source_quotes.get("GST"),
            ))

        # d) total == subtotal + gst
        expected_total = money(inv.subtotal + inv.gst)
        if abs(expected_total - inv.total) > TOLERANCE_SGD:
            results.append(_ev(
                "INV_ARITH", "fraud_signal", "high", doc, inv.page,
                f"Invoice total SGD {inv.total} does not equal subtotal "
                f"SGD {inv.subtotal} + GST SGD {inv.gst} = SGD {expected_total}.",
                expected=f"SGD {expected_total}",
                observed=f"SGD {inv.total}",
                quote=inv.source_quotes.get("Total"),
            ))

    return results


# 2. BANK_RUNNING_BAL ----------------------------------------------------

def check_bank_running_bal(pack: ExtractedPack) -> list[Evidence]:
    """Walk each statement's transactions and verify running balance. Each
    row is checked against the balance printed on the row before it, so one
    edited figure is one finding, not a gap carried into every later row."""
    results: list[Evidence] = []
    for stmt in pack.bank_statements:
        doc = stmt.doc_id
        previous = stmt.opening_balance

        for txn in stmt.transactions:
            running = previous
            if txn.credit is not None:
                running = money(running + txn.credit)
            if txn.debit is not None:
                running = money(running - txn.debit)
            previous = txn.balance

            if abs(running - txn.balance) > TOLERANCE_SGD:
                quote = stmt.source_quotes.get(txn.description, txn.description)
                results.append(_ev(
                    "BANK_RUNNING_BAL", "fraud_signal", "high", doc, stmt.page,
                    f"Running balance after transaction '{txn.description}' on "
                    f"{txn.date} should be SGD {running} but printed balance is "
                    f"SGD {txn.balance}.",
                    expected=f"SGD {running}",
                    observed=f"SGD {txn.balance}",
                    quote=quote,
                ))

        # Compare the last printed balance against stated closing balance
        if abs(previous - stmt.closing_balance) > TOLERANCE_SGD:
            results.append(_ev(
                "BANK_RUNNING_BAL", "fraud_signal", "high", doc, stmt.page,
                f"Closing balance SGD {stmt.closing_balance} is inconsistent with "
                f"the running total SGD {previous}.",
                expected=f"SGD {previous}",
                observed=f"SGD {stmt.closing_balance}",
                quote=stmt.source_quotes.get("Closing Balance"),
            ))

    return results


# BANK_PASS_THROUGH ------------------------------------------------------

_NAME_WORD = re.compile(r"[A-Z][A-Z&']*")
#: Fewest letters a party named in two bank descriptions has
_PARTY_MIN_LETTERS = 3


def party_words(description: str) -> list[str]:
    """The words of a bank description that may name a party, in order."""
    return [w for w in _NAME_WORD.findall(description.upper()) if w not in BANK_DESCRIPTION_WORDS]


def shared_party(a: str, b: str) -> str | None:
    """The party two bank descriptions both name: their longest common run of
    party words, or None when they share none (or only a word too short to be
    a name)."""
    left, right = party_words(a), party_words(b)
    best: list[str] = []
    for i in range(len(left)):
        for j in range(len(right)):
            k = 0
            while i + k < len(left) and j + k < len(right) and left[i + k] == right[j + k]:
                k += 1
            if k > len(best):
                best = left[i:i + k]
    name = " ".join(best)
    return name if sum(ch.isalpha() for ch in name) >= _PARTY_MIN_LETTERS else None


def check_bank_pass_through(pack: ExtractedPack) -> list[Evidence]:
    """Money that only passes through the account: credits paid on to another
    party within PASS_THROUGH_DAYS, keeping almost nothing (a genuine trader
    keeps a margin), that make up most of what the account pays out (it has
    little spending of its own: payroll, rent, suppliers it buys from). A
    recognised money-laundering pattern, and a sign the trade exists on paper.
    One finding per statement; money returned to the payer is a refund, not
    a pass-through."""
    results: list[Evidence] = []
    days = f"{PASS_THROUGH_DAYS} day{'' if PASS_THROUGH_DAYS == 1 else 's'}"
    for stmt in pack.bank_statements:
        rows = stmt.transactions
        used: set[int] = set()
        pairs: list[tuple[Transaction, Transaction]] = []
        for into in rows:
            if not into.credit or into.credit <= 0:
                continue
            out = next((i for i, t in enumerate(rows) if i not in used and t.debit and t.debit > 0
                        and into.date <= t.date <= into.date + timedelta(days=PASS_THROUGH_DAYS)
                        and PASS_THROUGH_MIN_SHARE * into.credit <= t.debit <= into.credit
                        and shared_party(into.description, t.description) is None), None)
            if out is not None:
                used.add(out)
                pairs.append((into, rows[out]))
        paid_out = sum((t.debit for t in rows if t.debit and t.debit > 0), Decimal(0))
        passed = sum((t.debit or Decimal(0) for _, t in pairs), Decimal(0))
        if not pairs or passed < PASS_THROUGH_MIN_VOLUME_SHARE * paid_out:
            continue
        received = sum((t.credit or Decimal(0) for t, _ in pairs), Decimal(0))
        into, out = pairs[0]
        results.append(_ev(
            "BANK_PASS_THROUGH", "fraud_signal", "medium", stmt.doc_id, stmt.page,
            f"{len(pairs)} credit(s) were paid on to another party within {days}, keeping under "
            f"{(1 - PASS_THROUGH_MIN_SHARE):.0%}: '{into.description}' SGD {into.credit} on {into.date} went out as "
            f"'{out.description}' SGD {out.debit} on {out.date}. These payments are {passed / paid_out:.0%} of "
            "everything the account paid out, so it shows little business of its own.",
            expected="a margin kept, or spending of the account's own",
            observed=f"SGD {passed} of SGD {received} ({passed / received:.0%}) paid on within {days}",
            quote=stmt.source_quotes.get(out.description, out.description),
        ))
    return results


# 3. BANK_CONTINUITY -----------------------------------------------------

def check_bank_continuity(pack: ExtractedPack) -> list[Evidence]:
    """For adjacent statement pairs, next opening must equal prev closing."""
    results: list[Evidence] = []
    stmts = sorted(pack.bank_statements, key=lambda s: s.period_start)

    for i in range(len(stmts) - 1):
        prev = stmts[i]
        nxt = stmts[i + 1]

        # Only check truly adjacent pairs
        if nxt.period_start != prev.period_end + timedelta(days=1):
            continue

        if abs(nxt.opening_balance - prev.closing_balance) > TOLERANCE_SGD:
            results.append(_ev(
                "BANK_CONTINUITY", "fraud_signal", "high",
                nxt.doc_id, nxt.page,
                f"Opening balance of {nxt.doc_id} (SGD {nxt.opening_balance}) "
                f"does not match closing balance of {prev.doc_id} "
                f"(SGD {prev.closing_balance}).",
                expected=f"SGD {prev.closing_balance}",
                observed=f"SGD {nxt.opening_balance}",
            ))

    return results


# 4. FS_BALANCE ----------------------------------------------------------

def check_fs_balance(pack: ExtractedPack) -> list[Evidence]:
    """total_assets == total_liabilities + total_equity within tolerance."""
    results: list[Evidence] = []
    for fs in pack.financial_statements:
        expected = money(fs.total_liabilities + fs.total_equity)
        if abs(fs.total_assets - expected) > TOLERANCE_SGD:
            results.append(_ev(
                "FS_BALANCE", "fraud_signal", "high", fs.doc_id, fs.page,
                f"Balance sheet does not balance: total assets SGD {fs.total_assets} "
                f"≠ liabilities SGD {fs.total_liabilities} + equity "
                f"SGD {fs.total_equity} = SGD {expected}.",
                expected=f"SGD {expected}",
                observed=f"SGD {fs.total_assets}",
            ))
    return results


# 5. XDOC_IDENTITY -------------------------------------------------------

def acra_record(pack: ExtractedPack) -> AcraProfile | None:
    """The ACRA record of the applicant the documents are held to: the one
    fetched from ACRA, else (records not fetched) the profile the applicant
    uploaded."""
    fetched = pack.official.applicant() if pack.official else None
    return fetched or next(iter(pack.acra_profiles), None)


def notices(pack: ExtractedPack) -> list[NoticeOfAssessment]:
    """Every Notice of Assessment the case holds: fetched from IRAS, and uploaded."""
    return [*(pack.official.notices if pack.official else []), *pack.notices_of_assessment]


def _reference_identity(pack: ExtractedPack) -> tuple[str, str, str] | None:
    """(UEN, name, where it is stated) of the company the pack's other
    documents are held to: the ACRA profile, else the first financial
    statements that state an identity. None if the pack states none."""
    profile = acra_record(pack)
    if profile is not None:
        return profile.uen, profile.company_name, "ACRA profile"
    for fs in pack.financial_statements:
        if fs.uen and fs.company_name:
            return fs.uen, fs.company_name, "FS"
    return None


def is_purchase(buyer_name: str, pack: ExtractedPack) -> bool:
    """Whether a trade document, by who it is addressed to, is the applicant's
    purchase: a supplier's invoice to it, or its own order to a supplier. Such a
    document's seller is a supplier, held to ACRA's record of that supplier by
    the registry checks; every other document is a sale by the applicant."""
    reference = _reference_identity(pack)
    return reference is not None and normalise_name(buyer_name) == normalise_name(reference[1])


def check_xdoc_identity(pack: ExtractedPack) -> list[Evidence]:
    """Cross-document identity checks against the company's registered
    identity: the ACRA profile if the pack has one, else the financial
    statements that state an identity."""
    results: list[Evidence] = []
    reference = _reference_identity(pack)
    if reference is None:
        return results

    ref_uen, ref_name, source = reference
    ref_name_norm = normalise_name(ref_name)

    sales = [inv for inv in pack.invoices if not is_purchase(inv.buyer_name, pack)]

    # UEN check on invoices (high, 1.0)
    for inv in sales:
        if inv.seller_uen != ref_uen:
            results.append(_ev(
                "XDOC_IDENTITY", "fraud_signal", "high", inv.doc_id, inv.page,
                f"Invoice seller UEN '{inv.seller_uen}' does not match the "
                f"{source} UEN '{ref_uen}'.",
                expected=ref_uen,
                observed=inv.seller_uen,
            ))

    # Name check on invoices (medium, 0.9)
    for inv in sales:
        inv_name_norm = normalise_name(inv.seller_name)
        if inv_name_norm != ref_name_norm:
            results.append(_ev(
                "XDOC_IDENTITY", "fraud_signal", "medium", inv.doc_id, inv.page,
                f"Invoice seller name '{inv.seller_name}' (normalised: "
                f"'{inv_name_norm}') does not match {source} company name "
                f"'{ref_name}' (normalised: '{ref_name_norm}').",
                expected=ref_name_norm,
                observed=inv_name_norm,
            ))

    # Name check on bank statements (medium, 0.9)
    for stmt in pack.bank_statements:
        holder_norm = normalise_name(stmt.account_holder)
        if holder_norm != ref_name_norm:
            results.append(_ev(
                "XDOC_IDENTITY", "fraud_signal", "medium", stmt.doc_id, stmt.page,
                f"Bank account holder '{stmt.account_holder}' (normalised: "
                f"'{holder_norm}') does not match {source} company name "
                f"'{ref_name}' (normalised: '{ref_name_norm}').",
                expected=ref_name_norm,
                observed=holder_norm,
            ))

    # Purchase orders: seller must be the applicant (high 1.0 / medium 0.9),
    # unless the applicant placed the order
    for po in (po for po in pack.purchase_orders if not is_purchase(po.buyer_name, pack)):
        if po.seller_uen != ref_uen:
            results.append(_ev(
                "XDOC_IDENTITY", "fraud_signal", "high", po.doc_id, po.page,
                f"Purchase order seller UEN '{po.seller_uen}' does not match the "
                f"{source} UEN '{ref_uen}'.",
                expected=ref_uen,
                observed=po.seller_uen,
            ))
        po_name_norm = normalise_name(po.seller_name)
        if po_name_norm != ref_name_norm:
            results.append(_ev(
                "XDOC_IDENTITY", "fraud_signal", "medium", po.doc_id, po.page,
                f"Purchase order seller name '{po.seller_name}' does not match "
                f"{source} company name '{ref_name}'.",
                expected=ref_name_norm,
                observed=po_name_norm,
            ))

    return results


# 6. XDOC_INVOICE_BANK ---------------------------------------------------

def check_xdoc_invoice_bank(pack: ExtractedPack) -> list[Evidence]:
    """For each invoice, find the bank payment that settled it within
    MATCH_WINDOW_DAYS: a credit for a sale, a debit for a purchase."""
    results: list[Evidence] = []

    # Every credit and debit across all statements, each usable at most once.
    # Tuple: (stmt_doc_id, transaction)
    available = {
        kind: [(stmt.doc_id, txn) for stmt in pack.bank_statements
               for txn in stmt.transactions if getattr(txn, kind) is not None]
        for kind in ("credit", "debit")
    }
    used: dict[str, set[int]] = {"credit": set(), "debit": set()}   # indices into available

    for inv in pack.invoices:
        kind = "debit" if is_purchase(inv.buyer_name, pack) else "credit"
        payments, taken = available[kind], used[kind]
        window_end = inv.invoice_date + timedelta(days=MATCH_WINDOW_DAYS)

        # Gather candidates: payments in date window
        candidates = [
            (idx, txn)
            for idx, (_, txn) in enumerate(payments)
            if idx not in taken
            and inv.invoice_date <= txn.date <= window_end
        ]

        # Find exact match (within tolerance)
        exact_idx = next(
            (idx for idx, txn in candidates if abs(getattr(txn, kind) - inv.total) <= TOLERANCE_SGD), None,
        )

        if exact_idx is not None:
            _, txn = payments[exact_idx]
            taken.add(exact_idx)
            # Check whether it's truly exact or just within tolerance
            if getattr(txn, kind) != inv.total:
                results.append(_ev(
                    "XDOC_INVOICE_BANK", "fraud_signal", "info",
                    inv.doc_id, inv.page,
                    f"Invoice {inv.invoice_number} total SGD {inv.total}: matching "
                    f"bank {kind} SGD {getattr(txn, kind)} on {txn.date} differs by "
                    f"SGD {abs(getattr(txn, kind) - inv.total):.2f} (within tolerance — "
                    f"likely a partial payment or rounding).",
                    expected=f"SGD {inv.total}",
                    observed=f"SGD {getattr(txn, kind)} on {txn.date}",
                ))
        else:
            # Find closest payment by amount (across all statements, not just window)
            unused = [txn for idx, (_, txn) in enumerate(payments) if idx not in taken]
            closest = min(unused, key=lambda txn: abs(getattr(txn, kind) - inv.total), default=None)
            closest_str = "none" if closest is None else f"SGD {getattr(closest, kind)} on {closest.date}"

            results.append(_ev(
                "XDOC_INVOICE_BANK", "fraud_signal", "high",
                inv.doc_id, inv.page,
                f"No bank {kind} matching invoice {inv.invoice_number} "
                f"(SGD {inv.total}) found within {MATCH_WINDOW_DAYS} days "
                f"after {inv.invoice_date}.",
                expected=(
                    f"{kind} of SGD {inv.total} between {inv.invoice_date} "
                    f"and {window_end}"
                ),
                observed=closest_str,
            ))

    return results


# 6b. XDOC_PO_INVOICE ----------------------------------------------------

def matching_po(inv: Invoice, pack: ExtractedPack) -> PurchaseOrder | None:
    """The purchase order an invoice relates to: the one it names (None if the
    pack lacks it), else one from the same buyer, with the closest total. None
    when that buyer placed no order."""
    if inv.po_number:
        return next((po for po in pack.purchase_orders if po.po_number == inv.po_number), None)
    buyer = normalise_name(inv.buyer_name)
    orders = [po for po in pack.purchase_orders if normalise_name(po.buyer_name) == buyer]
    return min(orders, key=lambda p: abs(p.total - inv.total)) if orders else None


def check_xdoc_po_invoice(pack: ExtractedPack) -> list[Evidence]:
    """Each invoice should be backed by a purchase order from the same buyer.

    Only runs when the pack contains purchase orders. Billing less than the
    order is normal (partial delivery); billing more, or billing a buyer with
    no order, is flagged. Billing before the order was placed is a date-order
    break, reported by TEMP_TRADE_SEQUENCE.
    """
    results: list[Evidence] = []
    if not pack.purchase_orders:
        return results

    for inv in pack.invoices:
        po = matching_po(inv, pack)
        if po is None:
            results.append(_ev(
                "XDOC_PO_INVOICE", "fraud_signal", "medium", inv.doc_id, inv.page,
                (f"Invoice {inv.invoice_number} names purchase order {inv.po_number}, which is not in the pack."
                 if inv.po_number else
                 f"Invoice {inv.invoice_number} bills '{inv.buyer_name}' but none of the "
                 f"{len(pack.purchase_orders)} purchase orders in the pack is from that buyer."),
                expected=f"purchase order {inv.po_number}" if inv.po_number else f"purchase order from {inv.buyer_name}",
                observed="none",
            ))
        elif inv.total - po.total > TOLERANCE_SGD:
            results.append(_ev(
                "XDOC_PO_INVOICE", "fraud_signal", "high", inv.doc_id, inv.page,
                f"Invoice {inv.invoice_number} total SGD {inv.total} exceeds purchase "
                f"order {po.po_number} total SGD {po.total} by SGD {inv.total - po.total}.",
                quote=inv.source_quotes.get("Total"),
                expected=f"at most SGD {po.total}",
                observed=f"SGD {inv.total}",
            ))

    return results


def check_xdoc_bl_invoice(pack: ExtractedPack) -> list[Evidence]:
    """Invoiced quantities should not exceed what the bill of lading (goods
    shipped abroad) or delivery order (goods delivered in Singapore) shows
    went to the buyer: billing for more goods than were shipped is the classic
    over-invoicing red flag in trade-based money laundering.

    Only runs when the pack contains either, which link to an invoice by their
    invoice reference. Invoice lines absent from them (services, which are
    never shipped) are not flagged.
    """
    results: list[Evidence] = []
    shipments = pack.shipments()
    if not shipments:
        return results

    for inv in pack.invoices:
        sent = [s for s in shipments if s.invoice_reference == inv.invoice_number]
        if not sent:
            results.append(_ev(
                "XDOC_BL_INVOICE", "fraud_signal", "medium", inv.doc_id, inv.page,
                f"No bill of lading or delivery order in the pack refers to invoice {inv.invoice_number}, "
                "so the goods billed cannot be traced to a shipment.",
                expected=f"bill of lading or delivery order referring to {inv.invoice_number}",
                observed="none",
            ))
            continue
        shipped: dict[str, tuple[int, Shipment]] = {}
        for shipment in sent:
            for g in shipment.goods:
                key = normalise_name(g.description)
                total, _ = shipped.get(key, (0, shipment))
                shipped[key] = (total + g.quantity, shipment)
        for item in inv.line_items:
            match = shipped.get(normalise_name(item.description))
            if match and item.quantity > match[0]:
                qty, shipment = match
                results.append(_ev(
                    "XDOC_BL_INVOICE", "fraud_signal", "high", inv.doc_id, inv.page,
                    f"Invoice {inv.invoice_number} bills {item.quantity} x '{item.description}' "
                    f"but {shipment.noun} {shipment.number} shows only {qty} {shipment.verb}.",
                    expected=f"at most {qty} {shipment.verb} ({shipment.number})",
                    observed=f"{item.quantity} invoiced",
                ))
    return results


# FS_ACRA_MISMATCH ------------------------------------------------------

#: Figures a financial statement and an ACRA profile both state, by the name
#: both models give them, with how an analyst reads them, in reading order:
#: profit and loss, balance sheet, cash flow
_ACRA_COMPARED = {
    "revenue": "Revenue",
    "ebit": "EBIT",
    "profit_before_tax": "Profit before tax",
    "profit_after_tax": "Profit after tax",
    "total_assets": "Total assets",
    "total_current_assets": "Total current assets",
    "total_liabilities": "Total liabilities",
    "total_current_liabilities": "Total current liabilities",
    "retained_earnings": "Retained earnings",
    "operating_cashflow": "Operating cash flow",
    "investing_cashflow": "Investing cash flow",
    "financing_cashflow": "Financing cash flow",
}


@dataclass(frozen=True)
class _Figure:
    """One figure as ACRA filed it and as submitted, with how far ACRA's
    printed rounding lets them differ."""

    field: str
    filed: Decimal
    submitted: Decimal
    rounding: Decimal

    @property
    def differs(self) -> bool:
        return abs(self.submitted - self.filed) > self.rounding


def _acra_figures(filed: AcraFinancials, fs: FinancialStatementSummary) -> list[_Figure]:
    """The figures both state, in reading order. ACRA prints rounded (to a
    thousandth of its unit), so a figure agrees if it rounds to what ACRA prints."""
    figures = []
    for field in _ACRA_COMPARED:
        printed, submitted = getattr(filed, field), getattr(fs, field)
        if printed is not None and submitted is not None:
            rounding = max(Decimal(10) ** printed.as_tuple().exponent / 2 * ACRA_FIGURES_UNIT, TOLERANCE_SGD)
            figures.append(_Figure(field, printed * ACRA_FIGURES_UNIT, submitted, rounding))
    return figures


def _common_factor(differ: list[_Figure]) -> Decimal | None:
    """The one factor every differing figure is of ACRA's, within ACRA's
    rounding; None when they share none, or too few differ to say. ACRA's
    true figure lies within its rounding of what it prints, so each figure
    allows the factors between submitted / (printed ± rounding); a common
    factor is one all of them allow (the middle of where they overlap)."""
    if len(differ) < ACRA_COMMON_FACTOR_MIN_FIGURES or any(abs(f.filed) <= f.rounding for f in differ):
        return None
    allowed = [sorted((f.submitted / (f.filed - f.rounding), f.submitted / (f.filed + f.rounding))) for f in differ]
    low, high = max(lo for lo, _ in allowed), min(hi for _, hi in allowed)
    return (low + high) / 2 if low <= high else None


def _times(ratio: Decimal) -> str:
    return f"{ratio:,.2f}×" if abs(ratio) >= Decimal("0.01") else f"{float(ratio):.3g}×"


def _acra_mismatch(fs: FinancialStatementSummary, compared: int, differ: list[_Figure]) -> Evidence:
    """One finding for every figure of one statement that differs from ACRA's:
    they are one fact about one document, decided once. Each figure stays a
    row with its own page and quote."""
    rows = [EvidenceRow(
        label=_ACRA_COMPARED[f.field], page=fs.figure_pages.get(f.field, fs.page),
        quote=fs.source_quotes.get(f.field), expected=f"SGD {f.filed:,.2f}", observed=f"SGD {f.submitted:,.2f}",
        ratio=_times(f.submitted / f.filed) if f.filed else None,
    ) for f in differ]
    year = f"for the year ended {fs.financial_year_end}"
    which = f"all {compared}" if len(differ) == compared else f"{len(differ)} of the {compared}"
    factor = _common_factor(differ)
    severity: Severity = "critical"
    if factor is not None and any(abs(factor / unit - 1) <= Decimal("0.01") for unit in ACRA_UNITS_FACTORS):
        severity = "medium"
        explanation = (f"The submitted financial statements are {_times(factor)} ACRA's filing on {which} figures "
                       f"compared, {year}: one set of figures stated in thousands where the other is in dollars, "
                       "or the reverse. Confirm the unit the statements are stated in.")
    elif factor is not None:
        explanation = (f"The submitted financial statements are {_times(factor)} ACRA's filing on {which} figures "
                       f"compared, {year}. One factor across every figure is the statement scaled as a whole, "
                       "not a slip in one figure.")
    elif len(differ) == 1:
        (row,) = rows
        explanation = (f"{row.label} in the submitted financial statements ({row.observed}) differs from the ACRA "
                       f"profile ({row.expected}) {year}.")
    else:
        explanation = (f"The submitted financial statements differ from ACRA's filing on {which} figures "
                       f"compared, {year}, by no common factor.")
    first = rows[0]
    return Evidence(
        check_id="FS_ACRA_MISMATCH", category="fraud_signal", severity=severity, document=fs.doc_id,
        page=first.page, quote=first.quote, expected=first.expected, observed=first.observed,
        method="deterministic", explanation=explanation, rows=rows if len(rows) > 1 else [],
    )


def check_fs_acra(pack: ExtractedPack) -> list[Evidence]:
    """The submitted financial statements state the figures ACRA holds for the
    same year. Statements for a different year than ACRA's latest are noted,
    not compared. A difference is critical: financials that contradict the
    statutory filing are a falsified document on their own. Every figure that
    differs is one finding on its statement, with a row per figure, and says
    when they share one factor (a units slip, at 1,000×, is medium)."""
    profile = acra_record(pack)
    filed = profile.financials if profile else None
    if filed is None:
        return []
    results: list[Evidence] = []
    for fs in pack.financial_statements:
        if fs.financial_year_end != filed.period_end:
            results.append(_ev(
                "FS_ACRA_MISMATCH", "fraud_signal", "info", fs.doc_id, fs.page,
                f"The financial statements are for the year ended {fs.financial_year_end}; the ACRA "
                f"profile's latest filed period ends {filed.period_end}. Their figures were not compared.",
            ))
            continue
        figures = _acra_figures(filed, fs)
        if differ := [f for f in figures if f.differs]:
            results.append(_acra_mismatch(fs, len(figures), differ))
    return results


# XDOC_BANK_CASH / XDOC_BANK_DEBT / XDOC_BANK_REVENUE --------------------

def _mentions(description: str, words: tuple[str, ...]) -> bool:
    return any(word in description.casefold() for word in words)


def _opens_after(pack: ExtractedPack, fs: FinancialStatementSummary) -> list[BankStatement]:
    """The bank statements from the one that opens the day after the balance
    sheet date, in date order; none if no statement does."""
    following = sorted(pack.bank_statements, key=lambda s: s.period_start)
    if following and following[0].period_start == fs.financial_year_end + timedelta(days=1):
        return following
    return []


def check_xdoc_bank_cash(pack: ExtractedPack) -> list[Evidence]:
    """The account the first statement shows is part of the company's cash, so
    it cannot open with more than the balance sheet declares the day before.
    (It may hold less: the company may bank elsewhere.)"""
    results: list[Evidence] = []
    for fs in pack.financial_statements:
        if fs.cash is None:
            continue
        for stmt in _opens_after(pack, fs)[:1]:
            if stmt.opening_balance > fs.cash + TOLERANCE_SGD:
                results.append(_ev(
                    "XDOC_BANK_CASH", "fraud_signal", "high", stmt.doc_id, stmt.page,
                    f"The bank statement opens on {stmt.period_start} with SGD {stmt.opening_balance:,.2f}, "
                    f"more than the SGD {fs.cash:,.2f} of cash the balance sheet declares at "
                    f"{fs.financial_year_end}. A bank account is part of the company's cash.",
                    quote=stmt.source_quotes.get("Opening Balance"),
                    expected=f"at most SGD {fs.cash:,.2f}",
                    observed=f"SGD {stmt.opening_balance:,.2f}",
                ))
    return results


def check_xdoc_bank_debt(pack: ExtractedPack) -> list[Evidence]:
    """Debt service in the bank statements is supported by debt: the
    borrowings on the balance sheet, plus money borrowed in the statements.
    Principal repaid cannot exceed the debt then outstanding, and interest on
    a loan or bond needs some debt outstanding."""
    results: list[Evidence] = []
    for fs in pack.financial_statements:
        statements = _opens_after(pack, fs)
        if fs.borrowings is None or not statements:
            continue
        debt = fs.borrowings
        rows = sorted(
            ((t.date, t.debit is not None, n, i, stmt, t)
             for n, stmt in enumerate(statements) for i, t in enumerate(stmt.transactions)),
            key=lambda row: row[:4],  # borrowed before repaid on the same day
        )
        for _, _, _, _, stmt, txn in rows:
            quote = stmt.source_quotes.get(txn.description, txn.description)
            if txn.credit and _mentions(txn.description, DEBT_INFLOW_WORDS):
                debt += txn.credit
            elif txn.debit and _mentions(txn.description, DEBT_REPAYMENT_WORDS):
                if txn.debit > debt + TOLERANCE_SGD:
                    results.append(_ev(
                        "XDOC_BANK_DEBT", "fraud_signal", "high", stmt.doc_id, stmt.page,
                        f"'{txn.description}' on {txn.date} repays SGD {txn.debit:,.2f}, but only "
                        f"SGD {max(debt, Decimal(0)):,.2f} of debt was outstanding: the balance sheet at "
                        f"{fs.financial_year_end} declares SGD {fs.borrowings:,.2f} and the statements show "
                        "no more borrowed before this date.",
                        quote=quote,
                        expected=f"at most SGD {max(debt, Decimal(0)):,.2f}",
                        observed=f"SGD {txn.debit:,.2f}",
                    ))
                debt = max(debt - txn.debit, Decimal(0))
            elif (txn.debit and _mentions(txn.description, DEBT_INTEREST_WORDS)
                  and _mentions(txn.description, DEBT_NAME_WORDS) and debt <= TOLERANCE_SGD):
                results.append(_ev(
                    "XDOC_BANK_DEBT", "fraud_signal", "medium", stmt.doc_id, stmt.page,
                    f"'{txn.description}' on {txn.date} pays interest on debt, but none was outstanding: "
                    f"the balance sheet at {fs.financial_year_end} declares SGD {fs.borrowings:,.2f} of "
                    "borrowings and the statements show none borrowed before this date.",
                    quote=quote,
                    observed=f"SGD {txn.debit:,.2f} interest",
                ))
    return results


def _filed_revenue(pack: ExtractedPack, fs: FinancialStatementSummary) -> Decimal | None:
    """The revenue ACRA holds for the statement's year, when it differs from the statement's."""
    profile = acra_record(pack)
    filed = profile.financials if profile else None
    if filed is None or filed.period_end != fs.financial_year_end:
        return None
    revenue = next((f for f in _acra_figures(filed, fs) if f.field == "revenue"), None)
    return revenue.filed if revenue and revenue.differs else None


def check_xdoc_bank_revenue(pack: ExtractedPack) -> list[Evidence]:
    """Sales receipts in the bank statements that follow the balance sheet
    date, annualised, are in line with the revenue the financial statements
    report. A wide gap either way means revenue is overstated, or money
    reaches the account that is not sales."""
    results: list[Evidence] = []
    for fs in pack.financial_statements:
        statements = _opens_after(pack, fs)
        days = sum((s.period_end - s.period_start).days + 1 for s in statements)
        if fs.revenue <= 0 or days < BANK_REVENUE_MIN_DAYS:
            continue
        receipts = sum(
            (t.credit for s in statements for t in s.transactions
             if t.credit and not _mentions(t.description, NON_SALES_CREDIT_WORDS)),
            Decimal(0),
        )
        annualised = receipts * 365 / days
        ratio = annualised / fs.revenue
        if not BANK_REVENUE_RATIO_MIN <= ratio <= BANK_REVENUE_RATIO_MAX:
            filed = _filed_revenue(pack, fs)
            against_filed = ""
            if filed:
                filed_ratio = annualised / filed
                against_filed = (
                    f" Against the SGD {filed:,.2f} of revenue filed with ACRA they are {filed_ratio:.2f} times, "
                    + ("within range: the bank statements support the filed figures, not the submitted ones."
                       if BANK_REVENUE_RATIO_MIN <= filed_ratio <= BANK_REVENUE_RATIO_MAX
                       else "also outside the range.")
                )
            results.append(_ev(
                "XDOC_BANK_REVENUE", "fraud_signal", "medium", fs.doc_id, fs.figure_pages.get("revenue", fs.page),
                f"Sales receipts in the bank statements (SGD {receipts:,.2f} over {days} days, "
                f"SGD {annualised:,.2f} a year) are {ratio:.2f} times the SGD {fs.revenue:,.2f} of revenue "
                f"the financial statements report, outside {BANK_REVENUE_RATIO_MIN} to {BANK_REVENUE_RATIO_MAX}."
                f"{against_filed}",
                quote=fs.source_quotes.get("revenue"),
                expected=f"{BANK_REVENUE_RATIO_MIN} to {BANK_REVENUE_RATIO_MAX} times revenue",
                observed=f"{ratio:.2f} times revenue",
            ))
    return results


# XDOC_ACRA_ENTITY / XDOC_ACRA_SIGNATORY ---------------------------------

def check_acra_entity(pack: ExtractedPack) -> list[Evidence]:
    """The entity self-certification states the company ACRA's profile
    describes: the legal name, the UEN (as the Singapore tax number) and the
    registered address (by postal code)."""
    profile = acra_record(pack)
    if profile is None:
        return []
    postal = re.findall(r"\b\d{6}\b", profile.registered_address)
    results: list[Evidence] = []
    for cert in pack.entity_self_certs:
        if cert.legal_name and normalise_name(cert.legal_name) != normalise_name(profile.company_name):
            results.append(_ev(
                "XDOC_ACRA_ENTITY", "fraud_signal", "high", cert.doc_id, 1,
                f"The legal name on {cert.doc_id} ('{cert.legal_name}') is not the name in the ACRA profile "
                f"('{profile.company_name}').",
                quote=cert.legal_name, expected=profile.company_name, observed=cert.legal_name,
            ))
        for row in cert.tax_rows:
            if (row.country or "").strip().upper() == "SINGAPORE" and row.tin and row.tin.strip() != profile.uen:
                results.append(_ev(
                    "XDOC_ACRA_ENTITY", "fraud_signal", "high", cert.doc_id, row.page,
                    f"Tax row {row.row} on {cert.doc_id} gives Singapore tax number '{row.tin}', but the ACRA "
                    f"profile's UEN is '{profile.uen}'. The UEN is generally the Singapore tax reference of an entity.",
                    quote=row.tin, expected=profile.uen, observed=row.tin,
                ))
        form_postal = cert.registered_address.postal_code
        if postal and form_postal and form_postal.strip() != postal[-1]:
            results.append(_ev(
                "XDOC_ACRA_ENTITY", "fraud_signal", "medium", cert.doc_id, 1,
                f"The registered address on {cert.doc_id} has postal code {form_postal}, but the ACRA profile's "
                f"registered office ('{profile.registered_address}') has {postal[-1]}.",
                quote=form_postal, expected=postal[-1], observed=form_postal,
            ))
    return results


#: Titles a Notice of Assessment prints before a name
_TITLES = frozenset({"MR", "MRS", "MS", "MDM", "MISS", "DR", "PROF"})


def _person_key(name: str) -> str:
    """A name to compare people by: no title, case, punctuation or extra spacing."""
    words = normalise_name(name).split()
    return " ".join(words[1:] if words and words[0] in _TITLES else words)


def check_acra_persons(pack: ExtractedPack) -> list[Evidence]:
    """A person on a Notice of Assessment or an individual form who is an
    officer in the ACRA profile (found by NRIC/FIN) is the same person: the
    name and the address (by postal code) agree. A notice fetched from IRAS
    is reported against the application as a whole: it is no uploaded document."""
    profile = acra_record(pack)
    if profile is None:
        return []
    officers = {_id_key(o.id_number): o for o in profile.officers}
    people = [(n.doc_id, n.name, n.id_number, n.postal_code) for n in notices(pack)] + [
        (c.doc_id, c.full_name, c.declaration_id_number, c.residential_address.postal_code)
        for c in pack.individual_self_certs
    ]
    results: list[Evidence] = []
    for doc_id, name, id_number, postal in people:
        officer = officers.get(_id_key(id_number or ""))
        if officer is None:
            continue
        who = f"{doc_id} gives {mask_id(id_number.strip()) if id_number else None}"
        anchor = PACK_DOC if doc_id == IRAS_DOC else doc_id
        if name and _person_key(name) != _person_key(officer.name):
            results.append(_ev(
                "XDOC_ACRA_PERSON", "fraud_signal", "high", anchor, 1,
                f"{who} to '{name}', but the ACRA profile lists that ID for '{officer.name}'.",
                quote=name, expected=officer.name, observed=name,
            ))
        if postal and officer.postal_code and postal.strip() != officer.postal_code:
            results.append(_ev(
                "XDOC_ACRA_PERSON", "fraud_signal", "medium", anchor, 1,
                f"{who} an address with postal code {postal}, but the ACRA profile gives "
                f"'{officer.name}' postal code {officer.postal_code}.",
                quote=postal, expected=officer.postal_code, observed=postal.strip(),
            ))
    return results


def check_acra_signatories(pack: ExtractedPack) -> list[Evidence]:
    """Each person who signs the entity self-certification is an officer
    listed in the ACRA profile."""
    profile = acra_record(pack)
    if profile is None:
        return []
    listed = ", ".join(o.name for o in profile.officers) or "no officers listed"
    return [
        _ev(
            "XDOC_ACRA_SIGNATORY", "fraud_signal", "medium", cert.doc_id, sig.page,
            f"Signatory '{sig.name}'{f' ({sig.designation})' if sig.designation else ''} on {cert.doc_id} is not "
            "an officer in the ACRA profile.",
            quote=sig.name, expected=listed, observed=sig.name,
        )
        for cert in pack.entity_self_certs
        for sig in cert.signatories
        if sig.name and all(similarity(sig.name, o.name) < SCREENING_MATCH_THRESHOLD for o in profile.officers)
    ]


# 7. META_MOD_AFTER_CREATE -----------------------------------------------

def check_meta_mod_after_create(pack: ExtractedPack) -> list[Evidence]:
    """Flag PDFs where mod_date - creation_date > META_MOD_GAP_HOURS. A
    workbook is edited after it is created as a matter of course. Low: benign
    re-saves (annotating, signing, compressing, filling a form) are common, so
    this corroborates a content finding rather than standing on its own."""
    results: list[Evidence] = []
    for doc in pack.docs:
        if doc.doc_type in FORM_DOC_TYPES or is_workbook(doc.filename):
            continue
        if doc.creation_date is None or doc.mod_date is None:
            continue
        delta_hours = (doc.mod_date - doc.creation_date).total_seconds() / 3600
        if delta_hours > META_MOD_GAP_HOURS:
            results.append(_ev(
                "META_MOD_AFTER_CREATE", "fraud_signal", "low",
                doc.doc_id, 1,
                f"PDF modification date ({doc.mod_date.date()}) is "
                f"{delta_hours:.0f} hours after creation date "
                f"({doc.creation_date.date()}), exceeding the "
                f"{META_MOD_GAP_HOURS}-hour threshold. "
                f"This is a weak signal; benign re-saves can also cause this.",
                expected=f"mod_date within {META_MOD_GAP_HOURS}h of creation_date",
                observed=f"delta = {delta_hours:.0f}h",
            ))
    return results


# 8. META_PRODUCER_MISMATCH ----------------------------------------------

def _issuers(pack: ExtractedPack) -> dict[str, str]:
    """doc_id -> the company that issued it, where the document says: an
    invoice's seller, an order's buyer, a delivery order's supplier. A bill
    of lading is the carrier's, and statements are the bank's or the
    applicant's: their type alone says who made them."""
    issuers = {
        **{i.doc_id: i.seller_name for i in pack.invoices},
        **{o.doc_id: o.buyer_name for o in pack.purchase_orders},
        **{s.doc_id: s.shipper_name for s in pack.shipments() if s.doc_id.startswith("delivery_order")},
    }
    return {doc_id: normalise_name(name) for doc_id, name in issuers.items() if name}


def check_meta_producer_mismatch(pack: ExtractedPack) -> list[Evidence]:
    """Among documents of one type from one issuer, any doc whose producer
    differs from their majority triggers a flag: a company makes its invoices
    with one system, so one made with other software may have been edited.
    Different issuers (the bank, IRAS, each supplier's own software) are never
    compared with each other, so a document is only compared when its issuer
    issued others of its type in the pack. Low: a different tool (a scan, a
    print to PDF) is common, so this only corroborates."""
    from collections import Counter

    issuers = _issuers(pack)
    results: list[Evidence] = []
    by_issuer: dict[tuple[str, str | None], list[DocMeta]] = {}
    for d in pack.docs:
        if d.doc_type not in FORM_DOC_TYPES and d.producer and not is_workbook(d.filename):
            by_issuer.setdefault((d.doc_type, issuers.get(d.doc_id)), []).append(d)

    for docs in by_issuer.values():
        majority_producer, _ = Counter(d.producer for d in docs).most_common(1)[0]
        for doc in docs:
            if doc.producer != majority_producer:
                results.append(_ev(
                    "META_PRODUCER_MISMATCH", "fraud_signal", "low",
                    doc.doc_id, 1,
                    f"PDF producer '{doc.producer}' differs from '{majority_producer}', "
                    f"which made the issuer's other {doc.doc_type.replace('_', ' ')} documents in the pack. "
                    f"This may indicate the file was edited or regenerated with different software.",
                    expected=majority_producer,
                    observed=doc.producer,
                ))

    return results


# ---------------------------------------------------------------------------
# B. CHECKLIST AND COVERAGE
# ---------------------------------------------------------------------------

# 9. BANK_COVERAGE -------------------------------------------------------

def check_bank_coverage(pack: ExtractedPack) -> list[Evidence]:
    """Statements must cover REQUIRED_BANK_MONTHS consecutive months (one
    statement may span several); the one ending last must not be stale
    relative to application_date."""
    results: list[Evidence] = []
    if not pack.bank_statements:
        results.append(_ev(
            "BANK_COVERAGE", "completeness", "medium",
            PACK_DOC, 1,
            "No bank statements found in the pack.",
            expected=f"{REQUIRED_BANK_MONTHS} months of statements",
            observed="0 statements",
        ))
        return results

    latest = max(pack.bank_statements, key=lambda s: s.period_end)

    # Build set of (year, month) present: every month a statement spans
    present: set[tuple[int, int]] = set()
    for s in pack.bank_statements:
        y, m = s.period_start.year, s.period_start.month
        while (y, m) <= (s.period_end.year, s.period_end.month):
            present.add((y, m))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    # Required months: REQUIRED_BANK_MONTHS ending with latest month
    latest_ym = (latest.period_end.year, latest.period_end.month)
    required: list[tuple[int, int]] = []
    y, m = latest_ym
    for _ in range(REQUIRED_BANK_MONTHS):
        required.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    required.reverse()

    missing = [ym for ym in required if ym not in present]
    if missing:
        missing_str = ", ".join(f"{y}-{m:02d}" for y, m in missing)
        results.append(_ev(
            "BANK_COVERAGE", "completeness", "medium",
            PACK_DOC, 1,
            f"Bank statements do not cover the required {REQUIRED_BANK_MONTHS} "
            f"consecutive months. Missing: {missing_str}.",
            expected=f"{REQUIRED_BANK_MONTHS} months ending {latest_ym[0]}-{latest_ym[1]:02d}",
            observed=f"Missing months: {missing_str}",
        ))

    # Recency check
    if pack.manifest is not None:
        app_date = pack.manifest.application_date
        days_stale = (app_date - latest.period_end).days
        if days_stale > BANK_RECENCY_DAYS:
            results.append(_ev(
                "BANK_COVERAGE", "completeness", "medium",
                latest.doc_id, latest.page,
                f"Latest bank statement ends {latest.period_end}, which is "
                f"{days_stale} days before application date {app_date} "
                f"(threshold: {BANK_RECENCY_DAYS} days).",
                expected=f"Statement end within {BANK_RECENCY_DAYS} days of {app_date}",
                observed=f"Latest period end: {latest.period_end} ({days_stale} days gap)",
            ))

    return results


# 10. CHECKLIST_MISSING_DOC ----------------------------------------------

def check_checklist_missing_doc(pack: ExtractedPack) -> list[Evidence]:
    """Check that each implemented checklist item has a corresponding document."""
    results: list[Evidence] = []
    doc_types_present = {d.doc_type for d in pack.docs}

    for item in CHECKLIST:
        if not item.get("implemented"):
            continue
        item_id = item["id"]
        label = item["label"]

        if item_id == "FATCA_CRS_ENTITY":
            if "fatca_crs_entity" not in doc_types_present:
                results.append(_ev(
                    "CHECKLIST_MISSING_DOC", "completeness", "high",
                    PACK_DOC, 1,
                    f"Required document '{label}' is missing from the pack.",
                    expected="fatca_crs_entity document present",
                    observed="not found",
                ))

        elif item_id == "BANK_6M":
            if "bank_statement" not in doc_types_present:
                results.append(_ev(
                    "CHECKLIST_MISSING_DOC", "completeness", "high",
                    PACK_DOC, 1,
                    f"Required document '{label}' is missing from the pack.",
                    expected="at least one bank_statement document present",
                    observed="not found",
                ))

        elif item_id == "NOA_GUARANTORS":
            for g in guarantors_without_noa(pack):
                masked = mask_id(g.id_number)
                results.append(_ev(
                    "CHECKLIST_MISSING_DOC", "completeness", "high",
                    PACK_DOC, 1,
                    f"Required document '{label}' is missing for guarantor {g.name} ({masked}).",
                    expected=f"a Notice of Assessment for {masked}",
                    observed="not found",
                ))

    return results


def _id_key(id_number: str) -> str:
    return "".join(id_number.split()).upper()


def guarantors_without_noa(pack: ExtractedPack) -> list[Guarantor]:
    """The application's guarantors with no Notice of Assessment, fetched from
    IRAS or uploaded, matched on NRIC/FIN (case and spaces ignored). None
    without application data."""
    if pack.manifest is None:
        return []
    covered = {_id_key(n.id_number) for n in notices(pack)}
    return [g for g in pack.manifest.guarantors if _id_key(g.id_number) not in covered]


# ---------------------------------------------------------------------------
# C. ENTITY FORM VALIDITY
# ---------------------------------------------------------------------------

def _cert_ev(
    check_id: str,
    severity: str,
    cert: EntitySelfCert,
    explanation: str,
    *,
    expected: str | None = None,
    observed: str | None = None,
    quote: str | None = None,
    page: int = 1,
) -> Evidence:
    return _ev(
        check_id, "compliance", severity, cert.doc_id, page,
        explanation,
        expected=expected,
        observed=observed,
        quote=quote,
    )


# 11. FORM_ENT_MANDATORY -------------------------------------------------

def check_form_ent_mandatory(pack: ExtractedPack) -> list[Evidence]:
    """legal_name, country_of_incorporation, street, postal_code, country present."""
    results: list[Evidence] = []
    for cert in pack.entity_self_certs:
        missing: list[str] = []
        if not cert.legal_name:
            missing.append("legal_name")
        if not cert.country_of_incorporation:
            missing.append("country_of_incorporation")
        addr = cert.registered_address
        if not addr.street:
            missing.append("registered street")
        if not addr.postal_code:
            missing.append("postal_code")
        if not addr.country:
            missing.append("country")
        if missing:
            results.append(_cert_ev(
                "FORM_ENT_MANDATORY", "medium", cert,
                f"Mandatory fields missing: {', '.join(missing)}.",
                expected="All mandatory fields populated",
                observed=f"Missing: {', '.join(missing)}",
            ))
    return results


# 12. FORM_ENT_MAILING ---------------------------------------------------

def check_form_ent_mailing(pack: ExtractedPack) -> list[Evidence]:
    """If mailing_different, mailing address fields required; cross-country needs explanation."""
    results: list[Evidence] = []
    for cert in pack.entity_self_certs:
        if not cert.mailing_different:
            continue
        m = cert.mailing_address
        missing: list[str] = []
        if not m.street:
            missing.append("mailing street")
        if not m.postal_code:
            missing.append("mailing postal_code")
        if not m.country:
            missing.append("mailing country")
        if missing:
            results.append(_cert_ev(
                "FORM_ENT_MAILING", "low", cert,
                f"Mailing address is marked different but required fields are "
                f"missing: {', '.join(missing)}.",
                expected="All mailing address fields populated",
                observed=f"Missing: {', '.join(missing)}",
            ))
        # Cross-country explanation
        reg_country = (cert.registered_address.country or "").strip().upper()
        mail_country = (m.country or "").strip().upper()
        if (
            mail_country
            and reg_country
            and mail_country != reg_country
            and not cert.mailing_explanation
        ):
            results.append(_cert_ev(
                "FORM_ENT_MAILING", "low", cert,
                "Mailing country differs from registered country but no "
                "explanation has been provided.",
                expected="mailing_explanation populated when countries differ",
                observed=f"registered={reg_country}, mailing={mail_country}, explanation absent",
            ))
    return results


# 13. FORM_ENT_BRANCH ----------------------------------------------------

def check_form_ent_branch(pack: ExtractedPack) -> list[Evidence]:
    """Exactly one option in branch_ticked; if 'Yes', hq_jurisdiction required."""
    results: list[Evidence] = []
    for cert in pack.entity_self_certs:
        if len(cert.branch_ticked) != 1:
            results.append(_cert_ev(
                "FORM_ENT_BRANCH", "medium", cert,
                f"Part 2 (branch question): exactly one option must be ticked, "
                f"but {len(cert.branch_ticked)} option(s) are ticked: "
                f"{cert.branch_ticked}.",
                expected="exactly one of [No, Yes] ticked",
                observed=str(cert.branch_ticked),
            ))
        elif cert.branch_ticked == ["Yes"] and not cert.hq_jurisdiction:
            results.append(_cert_ev(
                "FORM_ENT_BRANCH", "medium", cert,
                "Branch is 'Yes' but hq_jurisdiction is not provided.",
                expected="hq_jurisdiction populated",
                observed="hq_jurisdiction absent",
            ))
    return results


# 14. FORM_ENT_US_STATUS -------------------------------------------------

def check_form_ent_us_status(pack: ExtractedPack) -> list[Evidence]:
    """Exactly one option in us_person_ticked; if 'Yes', us_tin required."""
    results: list[Evidence] = []
    for cert in pack.entity_self_certs:
        if len(cert.us_person_ticked) != 1:
            results.append(_cert_ev(
                "FORM_ENT_US_STATUS", "high", cert,
                f"Part 3 (US person): exactly one option must be ticked, but "
                f"{len(cert.us_person_ticked)} option(s) are ticked: "
                f"{cert.us_person_ticked}.",
                expected="exactly one of [No, Yes] ticked",
                observed=str(cert.us_person_ticked),
            ))
        elif cert.us_person_ticked == ["Yes"] and not cert.us_tin:
            results.append(_cert_ev(
                "FORM_ENT_US_STATUS", "high", cert,
                "US person is 'Yes' but us_tin is not provided (W-9 is a manual check).",
                expected="us_tin populated",
                observed="us_tin absent",
            ))
    return results


# 15. FORM_ENT_TIN_OR_REASON ---------------------------------------------

def check_form_ent_tin_or_reason(pack: ExtractedPack) -> list[Evidence]:
    """Every tax row with a country has TIN or exactly one reason; B+ needs explanation.
    If us_person == No, at least one tax row required."""
    results: list[Evidence] = []
    for cert in pack.entity_self_certs:
        # At least one row if us_person is No
        if cert.us_person_ticked == ["No"] and not cert.tax_rows:
            results.append(_cert_ev(
                "FORM_ENT_TIN_OR_REASON", "medium", cert,
                "Entity is not a US person but no tax residency rows are provided.",
                expected="at least one tax residency row",
                observed="no tax rows",
            ))

        for row in cert.tax_rows:
            if not row.country:
                continue
            has_tin = bool(row.tin)
            num_reasons = len(row.reasons_ticked)
            if not has_tin and num_reasons == 0:
                results.append(_cert_ev(
                    "FORM_ENT_TIN_OR_REASON", "medium", cert,
                    f"Tax row {row.row} (country: {row.country}): "
                    f"no TIN and no reason ticked.",
                    expected="TIN or exactly one reason (A, B+, C)",
                    observed="neither TIN nor reason present",
                ))
            elif not has_tin and num_reasons > 1:
                results.append(_cert_ev(
                    "FORM_ENT_TIN_OR_REASON", "medium", cert,
                    f"Tax row {row.row} (country: {row.country}): "
                    f"multiple reasons ticked ({row.reasons_ticked}) without a TIN.",
                    expected="exactly one reason",
                    observed=str(row.reasons_ticked),
                ))
            if "B+" in row.reasons_ticked and not row.explanation:
                results.append(_cert_ev(
                    "FORM_ENT_TIN_OR_REASON", "medium", cert,
                    f"Tax row {row.row} (country: {row.country}): "
                    f"reason B+ ticked but no explanation provided.",
                    expected="explanation for reason B+",
                    observed="explanation absent",
                ))
    return results


# 16. FORM_ENT_TYPE_SELECTION --------------------------------------------

def check_form_ent_type_selection(pack: ExtractedPack) -> list[Evidence]:
    """Exactly one entity type A–M. A needs all giin_parts; D or M needs controlling person."""
    results: list[Evidence] = []
    for cert in pack.entity_self_certs:
        ticked = cert.entity_types_ticked
        if len(ticked) != 1:
            results.append(_cert_ev(
                "FORM_ENT_TYPE_SELECTION", "high", cert,
                f"Part 5 (entity type): exactly one type A–M must be ticked, "
                f"but {len(ticked)} type(s) are ticked: {ticked}.",
                expected="exactly one entity type A–M",
                observed=str(ticked),
            ))
            continue

        entity_type = ticked[0]

        # Type A: all three GIIN parts required
        if entity_type == "A":
            missing_giin = [
                f"giin_part[{i}]" for i, v in enumerate(cert.giin_parts) if not v
            ]
            if missing_giin:
                results.append(_cert_ev(
                    "FORM_ENT_TYPE_SELECTION", "high", cert,
                    f"Entity type A requires all three GIIN parts, but "
                    f"{missing_giin} are missing.",
                    expected="all three GIIN parts filled",
                    observed=f"Missing: {missing_giin}",
                ))

        # Type D or M: at least one controlling person with name + exactly one type
        if entity_type in ("D", "M"):
            valid_cps = [
                cp for cp in cert.controlling_persons
                if cp.name and len(cp.types_ticked) == 1
            ]
            if not valid_cps:
                results.append(_cert_ev(
                    "FORM_ENT_TYPE_SELECTION", "high", cert,
                    f"Entity type {entity_type} requires at least one controlling "
                    f"person with a name and exactly one type ticked.",
                    expected="at least one controlling person (name + one type)",
                    observed=f"{len(cert.controlling_persons)} controlling person(s) present",
                ))

    return results


# 17. FORM_ENT_DECLARATION -----------------------------------------------

def check_form_ent_declaration(pack: ExtractedPack) -> list[Evidence]:
    """At least one signatory with name, designation, and parseable date not after
    manifest.application_date."""
    results: list[Evidence] = []
    for cert in pack.entity_self_certs:
        if not cert.signatories:
            results.append(_cert_ev(
                "FORM_ENT_DECLARATION", "medium", cert,
                "No signatories found in Part 7 of the self-certification form.",
                expected="at least one signatory",
                observed="no signatories",
            ))
            continue

        valid_found = False
        for sig in cert.signatories:
            if not sig.name or not sig.designation:
                continue
            if sig.date is None:
                continue
            # Check date is not after application_date (if manifest available)
            if pack.manifest is not None:
                if sig.date > pack.manifest.application_date:
                    results.append(_cert_ev(
                        "FORM_ENT_DECLARATION", "medium", cert,
                        f"Signatory '{sig.name}' has a declaration date "
                        f"{sig.date} that is after the application date "
                        f"{pack.manifest.application_date}.",
                        expected=f"date ≤ {pack.manifest.application_date}",
                        observed=str(sig.date),
                    ))
                    continue
            valid_found = True

        if not valid_found and not results:
            # Emit a generic failure if no valid signatory and no date-error emitted
            results.append(_cert_ev(
                "FORM_ENT_DECLARATION", "medium", cert,
                "No valid signatory with name, designation and parseable date found.",
                expected="at least one complete signatory",
                observed="none found",
            ))

    return results


# 18. FORM_TEMPLATE_MATCH ------------------------------------------------

def check_form_template_match(pack: ExtractedPack) -> list[Evidence]:
    """template_id must not be None; otherwise flag the form as possibly outdated."""
    results: list[Evidence] = []
    for cert in pack.entity_self_certs:
        if cert.template_id is None:
            results.append(_cert_ev(
                "FORM_TEMPLATE_MATCH", "medium", cert,
                "The self-certification form could not be matched to the current "
                "GLDB template. The form may be outdated or altered. A current "
                "GLDB form should be requested from the customer.",
                expected="template_id populated",
                observed="template_id is None",
            ))
    return results


# ---------------------------------------------------------------------------
# D. ENTITY FORM REASONABLENESS AGAINST MANIFEST
# ---------------------------------------------------------------------------

def _require_manifest(
    pack: ExtractedPack,
    check_id: str,
) -> list[Evidence] | None:
    """Return a MANIFEST_MISSING evidence list if manifest is absent, else None."""
    if pack.manifest is None:
        return [_manifest_missing_evidence()]
    return None


# 19. FORM_ENT_NAME_MATCH ------------------------------------------------

def check_form_ent_name_match(pack: ExtractedPack) -> list[Evidence]:
    """normalise_name(legal_name) == normalise_name(manifest.company_name)."""
    missing = _require_manifest(pack, "FORM_ENT_NAME_MATCH")
    if missing is not None:
        return missing
    results: list[Evidence] = []
    for cert in pack.entity_self_certs:
        if not cert.legal_name:
            continue
        cert_norm = normalise_name(cert.legal_name)
        manifest_norm = normalise_name(pack.manifest.company_name)  # type: ignore[union-attr]
        if cert_norm != manifest_norm:
            results.append(_cert_ev(
                "FORM_ENT_NAME_MATCH", "high", cert,
                f"Form legal name '{cert.legal_name}' (normalised: '{cert_norm}') "
                f"does not match application manifest company name "
                f"'{pack.manifest.company_name}' (normalised: '{manifest_norm}').",  # type: ignore[union-attr]
                expected=manifest_norm,
                observed=cert_norm,
            ))
    return results


# 20. FORM_ENT_ADDRESS_MATCH ---------------------------------------------

def check_form_ent_address_match(pack: ExtractedPack) -> list[Evidence]:
    """Registered postal_code and country must equal manifest values."""
    missing = _require_manifest(pack, "FORM_ENT_ADDRESS_MATCH")
    if missing is not None:
        return missing
    results: list[Evidence] = []
    manifest: ApplicationManifest = pack.manifest  # type: ignore[assignment]
    for cert in pack.entity_self_certs:
        addr = cert.registered_address
        m_addr = manifest.registered_address

        if (addr.postal_code or "").strip() != (m_addr.postal_code or "").strip():
            results.append(_cert_ev(
                "FORM_ENT_ADDRESS_MATCH", "medium", cert,
                f"Registered postal code on form '{addr.postal_code}' does not "
                f"match manifest '{m_addr.postal_code}'.",
                expected=m_addr.postal_code,
                observed=addr.postal_code,
            ))

        if (addr.country or "").strip().upper() != (m_addr.country or "").strip().upper():
            results.append(_cert_ev(
                "FORM_ENT_ADDRESS_MATCH", "medium", cert,
                f"Registered country on form '{addr.country}' does not match "
                f"manifest '{m_addr.country}'.",
                expected=m_addr.country,
                observed=addr.country,
            ))

    return results


# 21. FORM_ENT_INCORPORATION ---------------------------------------------

def check_form_ent_incorporation(pack: ExtractedPack) -> list[Evidence]:
    """country_of_incorporation equals manifest.country_of_incorporation (case-insensitive)."""
    missing = _require_manifest(pack, "FORM_ENT_INCORPORATION")
    if missing is not None:
        return missing
    results: list[Evidence] = []
    manifest: ApplicationManifest = pack.manifest  # type: ignore[assignment]
    for cert in pack.entity_self_certs:
        if not cert.country_of_incorporation:
            continue
        if cert.country_of_incorporation.strip().upper() != manifest.country_of_incorporation.strip().upper():
            results.append(_cert_ev(
                "FORM_ENT_INCORPORATION", "medium", cert,
                f"Form country of incorporation '{cert.country_of_incorporation}' "
                f"does not match manifest '{manifest.country_of_incorporation}'.",
                expected=manifest.country_of_incorporation,
                observed=cert.country_of_incorporation,
            ))
    return results


# 22. FORM_ENT_SG_TIN ----------------------------------------------------

def check_form_ent_sg_tin(pack: ExtractedPack) -> list[Evidence]:
    """If a tax row country is Singapore and TIN present, it must equal manifest.uen."""
    missing = _require_manifest(pack, "FORM_ENT_SG_TIN")
    if missing is not None:
        return missing
    results: list[Evidence] = []
    manifest: ApplicationManifest = pack.manifest  # type: ignore[assignment]
    for cert in pack.entity_self_certs:
        for row in cert.tax_rows:
            if not row.country:
                continue
            if row.country.strip().upper() != "SINGAPORE":
                continue
            if not row.tin:
                continue
            if row.tin.strip() != manifest.uen.strip():
                results.append(_cert_ev(
                    "FORM_ENT_SG_TIN", "medium", cert,
                    f"Tax row {row.row}: Singapore TIN '{row.tin}' does not match "
                    f"the manifest UEN '{manifest.uen}'. The UEN is generally the "
                    f"Singapore tax reference for entities.",
                    expected=manifest.uen,
                    observed=row.tin,
                ))
    return results


# 23. FORM_ENT_START_UP --------------------------------------------------

def check_form_ent_start_up(pack: ExtractedPack) -> list[Evidence]:
    """If type H, entity age must be ≤ START_UP_MAX_MONTHS and revenue must be 0."""
    missing = _require_manifest(pack, "FORM_ENT_START_UP")
    if missing is not None:
        return missing
    results: list[Evidence] = []
    manifest: ApplicationManifest = pack.manifest  # type: ignore[assignment]
    for cert in pack.entity_self_certs:
        if "H" not in cert.entity_types_ticked:
            continue

        # Age check: use signatory date if available, else skip
        form_date: date | None = None
        for sig in cert.signatories:
            if sig.date is not None:
                form_date = sig.date
                break

        if form_date is not None:
            doi = manifest.date_of_incorporation
            # Approximate months difference
            months_diff = (form_date.year - doi.year) * 12 + (form_date.month - doi.month)
            if months_diff > START_UP_MAX_MONTHS:
                results.append(_cert_ev(
                    "FORM_ENT_START_UP", "medium", cert,
                    f"Entity type H (start-up) claimed, but the entity is approximately "
                    f"{months_diff} months old (incorporated {doi}), exceeding the "
                    f"{START_UP_MAX_MONTHS}-month threshold.",
                    expected=f"entity age ≤ {START_UP_MAX_MONTHS} months",
                    observed=f"≈ {months_diff} months",
                ))

        # Revenue check: if any FS shows revenue > 0
        for fs in pack.financial_statements:
            if fs.revenue > Decimal("0"):
                results.append(_cert_ev(
                    "FORM_ENT_START_UP", "medium", cert,
                    f"Entity type H (start-up, no prior operating history) is claimed, "
                    f"but financial statements show revenue of SGD {fs.revenue}.",
                    expected="revenue = 0 for a start-up (type H)",
                    observed=f"SGD {fs.revenue}",
                ))

    return results


# 24. FORM_ENT_SIGNATORY -------------------------------------------------

def check_form_ent_signatory(pack: ExtractedPack) -> list[Evidence]:
    """Each signatory's normalised name is in manifest.authorised_signatories."""
    missing = _require_manifest(pack, "FORM_ENT_SIGNATORY")
    if missing is not None:
        return missing
    results: list[Evidence] = []
    manifest: ApplicationManifest = pack.manifest  # type: ignore[assignment]
    authorised_norm = {normalise_name(n) for n in manifest.authorised_signatories}

    for cert in pack.entity_self_certs:
        for sig in cert.signatories:
            if not sig.name:
                continue
            sig_norm = normalise_name(sig.name)
            if sig_norm not in authorised_norm:
                results.append(_cert_ev(
                    "FORM_ENT_SIGNATORY", "medium", cert,
                    f"Signatory '{sig.name}' (normalised: '{sig_norm}') is not in "
                    f"the list of authorised signatories from the application: "
                    f"{manifest.authorised_signatories}.",
                    expected=f"signatory in {manifest.authorised_signatories}",
                    observed=sig.name, page=sig.page,
                ))
    return results


# ---------------------------------------------------------------------------
# F. FORENSICS (inspect the raw file, see gldb.forensics)
# ---------------------------------------------------------------------------

def _forensic_docs(pack: ExtractedPack):
    """(doc_id, path) for PDFs we can inspect. Official form templates are
    skipped: they legitimately use white headings and mixed fonts. Workbooks
    are skipped: they are not PDFs."""
    for doc in pack.docs:
        path = pack.paths.get(doc.doc_id)
        if path is not None and doc.doc_type not in FORM_DOC_TYPES and not is_workbook(path):
            yield doc.doc_id, path


def check_forensic_hidden_text(pack: ExtractedPack) -> list[Evidence]:
    return [
        _ev(
            "FORENSIC_HIDDEN_TEXT", "fraud_signal", "high", doc_id, run.page,
            f"{doc_id} page {run.page} contains text a reader cannot see "
            f"({', '.join(run.reasons)}). Hidden text can carry instructions aimed "
            "at automated review or conceal edits.",
            quote=run.text[:HIDDEN_QUOTE_MAX_CHARS],
            observed=", ".join(run.reasons),
        )
        for doc_id, path in _forensic_docs(pack)
        for run in hidden_text(path)
    ]


def check_forensic_mixed_font(pack: ExtractedPack) -> list[Evidence]:
    return [
        _ev(
            "FORENSIC_MIXED_FONT", "fraud_signal", "medium", doc_id, token.page,
            f"'{token.text}' in {doc_id} page {token.page} mixes fonts or sizes within "
            "one token, which can mean a figure was edited on top of the original.",
            quote=token.text, observed=" / ".join(token.fonts),
        )
        for doc_id, path in _forensic_docs(pack)
        for token in mixed_font_tokens(path)
    ]


def check_forensic_incremental_update(pack: ExtractedPack) -> list[Evidence]:
    """PDFs saved again after their first save. Low, like the metadata
    checks: filling a form or signing appends a revision too, so this
    corroborates a content finding rather than standing on its own."""
    results = []
    for doc_id, path in _forensic_docs(pack):
        revisions = incremental_updates(path)
        if revisions:
            results.append(_ev(
                "FORENSIC_INCREMENTAL_UPDATE", "fraud_signal", "low", doc_id, 1,
                f"{doc_id} was saved again after it was created ({revisions} later "
                "revision(s)), so its content may have been changed.",
                observed=f"{revisions} later revision(s)",
            ))
    return results


def check_forensic_ocr_mismatch(pack: ExtractedPack) -> list[Evidence]:
    """Amounts in the visible text layer vs amounts Textract sees on the page.

    Hidden (white, tiny, off-page) text is excluded from the text layer here;
    FORENSIC_HIDDEN_TEXT already reports it. Without OCR the comparison cannot
    run, which is reported as an info finding rather than passed silently.
    """
    if not pack.ocr:
        return [_ev(
            "FORENSIC_OCR_SKIPPED", "fraud_signal", "info", PACK_DOC, 1,
            "The text layer vs OCR comparison did not run because no page was read by OCR (OCR is "
            "turned off, or the case holds only forms and workbooks). "
            "Figures covered or pasted over as images would not be detected.",
        )]
    results = []
    for doc_id, path in _forensic_docs(pack):
        seen = pack.ocr.get(doc_id)
        if seen is None:
            continue
        # The file's own text: its visible text layer and, for a fillable form, its field values
        for m in amount_mismatches(model_view(path), seen):
            results.append(_ev(
                "FORENSIC_OCR_MISMATCH", "fraud_signal", "medium", doc_id, m.page,
                f"On {doc_id} page {m.page}, the amounts a reader sees differ from the "
                "amounts stored in the file. A figure may be covered, or pasted over the "
                "original as an image. OCR can misread, so compare with the source.",
                expected=", ".join(m.covered) or None,
                observed=", ".join(m.pasted) or None,
            ))
    return results


def check_prompt_injection(pack: ExtractedPack) -> list[Evidence]:
    """Pages the prompt attack screen flagged: the text a model would read
    contains instructions aimed at it. Info PROMPT_SCREEN_SKIPPED when the
    screen did not run, so its absence is visible."""
    if pack.prompt_screen is None:
        return [_ev(
            "PROMPT_SCREEN_SKIPPED", "fraud_signal", "info", PACK_DOC, 1,
            "Document text was not screened for prompt attacks (PROMPT_SCREEN_ENABLED is off).",
        )]
    return [
        _ev(
            "PROMPT_INJECTION", "fraud_signal", "high", doc_id, hit.page,
            f"The text on page {hit.page} of {doc_id} contains instructions aimed at an AI "
            f"model (prompt attack screen, {hit.confidence} confidence). A genuine business "
            "document has no reason to address an automated reviewer.",
            expected="no instructions addressed to a model",
            observed=f"prompt attack detected ({hit.confidence} confidence)",
        )
        for doc_id, hits in pack.prompt_screen.items()
        for hit in hits
    ]


# ---------------------------------------------------------------------------
# G. REGISTRY & SCREENING (ACRA's records, fetched by gldb.official; the
# versioned sanctions list, see gldb.reference)
# ---------------------------------------------------------------------------

def registered_entities(pack: ExtractedPack) -> list[tuple[str, int, str, str, str]]:
    """(doc_id, page, role, UEN, name) for every company identified by UEN."""
    found = []
    if pack.manifest is not None:
        found.append((PACK_DOC, 1, "applicant", pack.manifest.uen,
                      pack.manifest.company_name))
    found += [(i.doc_id, i.page, "seller", i.seller_uen, i.seller_name) for i in pack.invoices]
    found += [(p.doc_id, p.page, "seller", p.seller_uen, p.seller_name) for p in pack.purchase_orders]
    found += [(f.doc_id, f.page, "company", f.uen, f.company_name)
              for f in pack.financial_statements if f.uen and f.company_name]
    return found


def _live(record: AcraProfile) -> bool:
    return record.status.strip().upper().startswith("LIVE")


def check_official_records(pack: ExtractedPack) -> list[Evidence]:
    """The case was checked against ACRA's and IRAS's own records. Info when
    the records stage is switched off; an open item when the service did not
    answer, as nothing was checked against ACRA or IRAS."""
    if pack.official is None:
        return [_ev(
            "OFFICIAL_RECORDS_UNAVAILABLE", "completeness", "info", PACK_DOC, 1,
            "ACRA and IRAS records were not fetched (OFFICIAL_RECORDS_ENABLED is off); the documents are held "
            "to an uploaded ACRA profile and uploaded notices only.",
        )]
    if pack.official.error is None:
        return []
    return [_ev(
        "OFFICIAL_RECORDS_UNAVAILABLE", "completeness", "medium", PACK_DOC, 1,
        f"ACRA and IRAS records could not be fetched: {pack.official.error}. Nothing was checked against "
        "them; run the case again once the service answers.",
        expected="ACRA and IRAS records fetched", observed="service unavailable",
    )]


#: What an uploaded ACRA profile states that ACRA's record states too
_PROFILE_FIELDS = ("company_name", "uen", "incorporation_date", "status", "registered_address")
#: The tax computation an uploaded notice states that IRAS's record states too
_NOTICE_FIGURES = ("income", "deductions", "chargeable_income", "tax_payable")


def _text_key(value: object) -> str:
    return " ".join(str(value).upper().split())


def _altered(doc_id: str, page: int, record: str, field: str, original: object, copy: object) -> Evidence:
    return _ev(
        "XDOC_OFFICIAL_RECORD", "fraud_signal", "critical", doc_id, page,
        f"The {field.replace('_', ' ')} on {doc_id} is '{copy}', but {record} has '{original}'. The uploaded "
        "copy of an official record differs from the record itself.",
        quote=str(copy), expected=str(original), observed=str(copy),
    )


def check_official_copies(pack: ExtractedPack) -> list[Evidence]:
    """An ACRA profile or Notice of Assessment the applicant uploaded states
    what ACRA's or IRAS's own record states: a copy that differs was altered.
    A notice for another year than IRAS's latest is noted, not compared. A
    difference is critical: an edited government record is a falsified
    document on its own, like financials that contradict ACRA's filing."""
    if pack.official is None or pack.official.error is not None:
        return []
    results: list[Evidence] = []
    version = _acra_version(pack)
    for copy in pack.acra_profiles:
        original = pack.official.companies.get(copy.uen) or pack.official.applicant()
        if original is None:
            continue
        where = f"ACRA's record ({version})"
        results += [_altered(copy.doc_id, copy.page, where, f, getattr(original, f), getattr(copy, f))
                    for f in _PROFILE_FIELDS if _text_key(getattr(copy, f)) != _text_key(getattr(original, f))]
        listed = {_id_key(o.id_number) for o in original.officers}
        results += [
            _ev("XDOC_OFFICIAL_RECORD", "fraud_signal", "critical", copy.doc_id, copy.page,
                f"{copy.doc_id} lists '{o.name}' ({o.position}, {mask_id(o.id_number)}) as an officer; {where} does not.",
                quote=o.name, expected=", ".join(x.name for x in original.officers) or "no officers",
                observed=o.name)
            for o in copy.officers if _id_key(o.id_number) not in listed
        ]
        if copy.financials and original.financials:
            results += [
                _altered(copy.doc_id, copy.page, where, f, getattr(original.financials, f), getattr(copy.financials, f))
                for f in type(copy.financials).model_fields
                if getattr(copy.financials, f) != getattr(original.financials, f)
            ]
    held = {_id_key(n.id_number): n for n in pack.official.notices}
    for uploaded in pack.notices_of_assessment:
        notice = held.get(_id_key(uploaded.id_number))
        if notice is None:
            continue
        where = f"IRAS's record ({pack.official.versions.get('iras', 'IRAS')})"
        if uploaded.year_of_assessment != notice.year_of_assessment:
            results.append(_ev(
                "XDOC_OFFICIAL_RECORD", "fraud_signal", "info", uploaded.doc_id, uploaded.page,
                f"{uploaded.doc_id} is the notice for Year of Assessment {uploaded.year_of_assessment}; {where} "
                f"is for {notice.year_of_assessment}, so the two were not compared.",
            ))
            continue
        if _person_key(uploaded.name) != _person_key(notice.name):
            results.append(_altered(uploaded.doc_id, uploaded.page, where, "name", notice.name, uploaded.name))
        results += [
            _altered(uploaded.doc_id, uploaded.page, where, f, getattr(notice, f), getattr(uploaded, f))
            for f in (*_NOTICE_FIGURES, "postal_code")
            if getattr(uploaded, f) is not None and getattr(uploaded, f) != getattr(notice, f)
        ]
    return results


def _acra_version(pack: ExtractedPack) -> str:
    return pack.official.versions.get("acra", "ACRA") if pack.official else "ACRA"


def check_registry_entities(pack: ExtractedPack) -> list[Evidence]:
    """Each UEN the documents give is registered with ACRA, under the name
    used, and live. What ACRA says of a company is one finding however many
    documents name it, on the first, which lists the others; a wrong name is
    one finding per name."""
    if pack.official is None or pack.official.error is not None:
        return []
    version = _acra_version(pack)
    results = []
    applicant = pack.official.applicant_uen
    by_uen: dict[str, list[tuple[str, int, str, str]]] = {}
    for doc_id, page, role, uen, name in registered_entities(pack):
        by_uen.setdefault(uen.strip(), []).append((doc_id, page, role, name))
    if applicant and applicant not in by_uen and pack.official.applicant() is None:
        # Given only on the forms (no application data): reported on the form that gives it
        form = next((c.doc_id for c in pack.entity_self_certs
                     for row in c.tax_rows if (row.tin or "").strip() == applicant), PACK_DOC)
        results.append(_ev(
            "REGISTRY_ENTITY_NOT_FOUND", "fraud_signal", "high", form, 1,
            f"The applicant's UEN {applicant} is not registered with ACRA ({version}).",
            expected="a registered UEN", observed=f"UEN {applicant}",
        ))
    for uen, mentions in by_uen.items():
        entity = pack.official.companies.get(uen)
        doc_id, page, role, name = mentions[0]
        where = f"the {role} on {doc_id}" if doc_id != PACK_DOC else "the applicant"
        elsewhere = list(dict.fromkeys(d for d, *_ in mentions if d not in (doc_id, PACK_DOC)))
        also = f" It is also named on {', '.join(elsewhere)}." if elsewhere else ""
        if entity is None:
            results.append(_ev(
                "REGISTRY_ENTITY_NOT_FOUND", "fraud_signal", "high", doc_id, page,
                f"UEN {uen} given for {where} ('{name}') is not registered with ACRA ({version}).{also}",
                expected="a registered UEN", observed=f"UEN {uen}",
            ))
            continue
        first_with_name: dict[str, tuple[str, int, str, str]] = {}
        for mention in mentions:
            first_with_name.setdefault(mention[3], mention)
        for doc, doc_page, doc_role, used in first_with_name.values():
            if similarity(used, entity.company_name) < SCREENING_MATCH_THRESHOLD:
                results.append(_ev(
                    "REGISTRY_NAME_MISMATCH", "fraud_signal", "high", doc, doc_page,
                    f"UEN {uen} is registered to '{entity.company_name}', but "
                    f"{f'the {doc_role} on {doc}' if doc != PACK_DOC else 'the applicant'} names '{used}' ({version}).",
                    expected=entity.company_name, observed=used,
                ))
        if not _live(entity):
            results.append(_ev(
                "REGISTRY_ENTITY_INACTIVE", "fraud_signal", "high", doc_id, page,
                f"'{entity.company_name}' (UEN {uen}) is '{entity.status}' at ACRA "
                f"({version}), so it cannot trade.{also}",
                expected="LIVE COMPANY", observed=entity.status,
            ))
    return results


def check_registry_signatories(pack: ExtractedPack) -> list[Evidence]:
    """Authorised signatories should be registered officers of the applicant."""
    if pack.manifest is None or pack.official is None:
        return []
    entity = pack.official.applicant()
    if entity is None:
        return []  # REGISTRY_ENTITY_NOT_FOUND already reports it
    return [
        _ev(
            "REGISTRY_SIGNATORY_NOT_OFFICER", "fraud_signal", "medium",
            PACK_DOC, 1,
            f"Authorised signatory '{person}' is not a registered officer of "
            f"'{entity.company_name}' ({_acra_version(pack)}).",
            expected=", ".join(o.name for o in entity.officers), observed=person,
        )
        for person in pack.manifest.authorised_signatories
        if all(similarity(person, o.name) < SCREENING_MATCH_THRESHOLD for o in entity.officers)
    ]


def _screened_names(pack: ExtractedPack) -> dict[str, tuple[str, str, int, str]]:
    """screening key -> (name, doc_id, page, role), first mention wins."""
    mentions: list[tuple[str, str, int, str]] = []
    if pack.manifest is not None:
        m = pack.manifest
        mentions.append((m.company_name, PACK_DOC, 1, "applicant"))
        mentions += [(n, PACK_DOC, 1, "signatory") for n in m.authorised_signatories]
        mentions += [(g.name, PACK_DOC, 1, "guarantor") for g in m.guarantors]
    for i in pack.invoices:
        mentions += [(i.seller_name, i.doc_id, i.page, "seller"), (i.buyer_name, i.doc_id, i.page, "buyer")]
    for p in pack.purchase_orders:
        mentions += [(p.buyer_name, p.doc_id, p.page, "buyer"), (p.seller_name, p.doc_id, p.page, "seller")]
    for b in pack.shipments():
        mentions += [(b.shipper_name, b.doc_id, b.page, "shipper"),
                     (b.consignee_name, b.doc_id, b.page, "consignee")]
    # The people of the applicant and of every company the documents name, as ACRA lists them
    official = pack.official
    records = official.companies if official else {}
    applicant = official.applicant_uen if official else None
    for uen, record in sorted(records.items(), key=lambda found: found[0] != applicant):
        if record is None:
            continue
        of = "" if uen == applicant else f" of {record.company_name}"
        mentions += [(o.name, PACK_DOC, 1, f"officer ({o.position.lower()}){of} at ACRA") for o in record.officers]
        mentions += [(h.name, PACK_DOC, 1, f"shareholder{of} at ACRA") for h in record.shareholders]
    # ... and as the applicant's forms name them: who controls it, who signs for it, who declares
    for c in pack.entity_self_certs:
        mentions += [(p.name, c.doc_id, p.page, "controlling person") for p in c.controlling_persons if p.name]
    mentions += [(s.name, c.doc_id, 1, "signatory") for c in pack.entity_self_certs for s in c.signatories if s.name]
    mentions += [(c.full_name, c.doc_id, 1, "declarant") for c in pack.individual_self_certs if c.full_name]
    names: dict[str, tuple[str, str, int, str]] = {}
    for name, doc_id, page, role in mentions:
        names.setdefault(screening_key(name), (name, doc_id, page, role))
    return names


def check_sanctions(pack: ExtractedPack) -> list[Evidence]:
    """Screen the applicant, its people (from the application, ACRA's record
    and the forms), its counterparties and their people at ACRA against the
    sanctions list. A hit is a possible match for a person to dispose of.
    Where the individual form gives the person's date of birth and the list
    the entry's, a date that fits none of the entry's discounts the hit (kept
    as information, so the analyst sees it was screened); one that fits is
    said to."""
    wl = watchlist()
    born = {screening_key(c.full_name): c.date_of_birth
            for c in pack.individual_self_certs if c.full_name and c.date_of_birth}
    results = []
    for key, (name, doc_id, page, role) in _screened_names(pack).items():
        for entry, score, matched in screen(name):
            listed = f"'{entry.name}'" + ("" if matched == entry.name else f" (alias '{matched}')")
            fits = birth_date_fits(born.get(key), entry)
            severity: Severity = "high"
            then = "Confirm or dismiss the match before proceeding."
            if fits is not None:
                dates = f"born {born[key]:%d %b %Y} by the individual form; the entry lists {', '.join(entry.birth_dates)}"
                if fits:
                    then = f"The date of birth fits ({dates}). {then}"
                else:
                    severity, then = "info", (f"Discounted: {dates}. No action is needed unless other details "
                                              f"(nationality, address, identity number) match the entry.")
            results.append(_ev(
                "SANCTIONS_MATCH", "fraud_signal", severity, doc_id, page,
                f"The {role} '{name}' resembles {listed} on {entry.list} "
                f"({entry.reference}), similarity {score:.2f}. Sanctions list {wl.version}, "
                f"as of {wl.as_of}. {then}",
                quote=name,
                expected="no sanctions match",
                observed=f"'{name}' ~ {listed} ({entry.list} {entry.reference})",
            ))
    return results


#: Severity of a FATF-listed country. A call for action is critical, which on
#: its own puts the case in the High band and on enhanced review; increased
#: monitoring is high, which on its own is the Medium band and standard review.
_COUNTRY_SEVERITY: dict[str, Severity] = {"call_for_action": "critical", "increased_monitoring": "high"}
_COUNTRY_LISTS = {
    "call_for_action": ("FATF's list of high-risk jurisdictions subject to a call for action",
                        "Apply enhanced due diligence and confirm why the applicant is connected with it."),
    "increased_monitoring": ("FATF's list of jurisdictions under increased monitoring",
                             "Take the added risk into account and confirm why the applicant is connected with it."),
}


def _country_mentions(pack: ExtractedPack) -> list[tuple[str | None, str, int, str]]:
    """(country as written, doc_id, page, what it is the country of) for the
    registered address and each tax residence the application and its forms give."""
    mentions: list[tuple[str | None, str, int, str]] = []
    if pack.manifest is not None:
        mentions.append((pack.manifest.registered_address.country, PACK_DOC, 1, "registered address"))
    for entity in pack.entity_self_certs:
        mentions.append((entity.registered_address.country, entity.doc_id, 1, "registered address"))
        mentions += [(row.country, entity.doc_id, row.page, "tax residence") for row in entity.tax_rows]
    for person in pack.individual_self_certs:
        mentions += [(row.country, person.doc_id, row.page, "tax residence") for row in person.tax_rows]
    return mentions


def check_high_risk_countries(pack: ExtractedPack) -> list[Evidence]:
    """The countries the application and its forms give for the registered
    address and for tax residence, against FATF's lists of high-risk
    jurisdictions. One finding for each listed country in each document."""
    lists = high_risk_countries()
    found: dict[tuple[str, str], tuple[HighRiskCountry, int, str, list[str]]] = {}
    for written, doc_id, page, what in _country_mentions(pack):
        if written and (country := high_risk_country(written)):
            found.setdefault((doc_id, country.name), (country, page, written.strip(), []))[3].append(what)
    results = []
    for (doc_id, _), (country, page, written, whats) in found.items():
        listed, advice = _COUNTRY_LISTS[country.fatf_list]
        source = "The application" if doc_id == PACK_DOC else doc_id
        results.append(_ev(
            "HIGH_RISK_COUNTRY", "fraud_signal", _COUNTRY_SEVERITY[country.fatf_list], doc_id, page,
            f"{source} gives '{written}' as the {' and '.join(dict.fromkeys(whats))}, which is on {listed} "
            f"(FATF {lists.version}, as of {lists.as_of}). {advice}",
            quote=written, expected="no high-risk country", observed=f"'{written}' ({country.name}, {country.fatf_list})",
        ))
    return results


# ---------------------------------------------------------------------------
# H. TEMPORAL CONSISTENCY
# Evidence "observed" holds the date found wrong, so gldb.timeline can mark
# the exact event a finding is about.
# ---------------------------------------------------------------------------

def incorporation(pack: ExtractedPack) -> tuple[date, str] | None:
    """The applicant's incorporation date and its source: ACRA's record when
    it has one, otherwise the application; None with neither."""
    entity = pack.official.applicant() if pack.official else None
    if entity is not None:
        return entity.incorporation_date, f"ACRA ({_acra_version(pack)})"
    if pack.manifest is not None:
        return pack.manifest.date_of_incorporation, "the application"
    return None


def dated_facts(pack: ExtractedPack) -> list[TimelineEvent]:
    """Every dated fact the pack states, unsorted. Trade facts carry the
    invoice they belong to as *ref*."""
    facts: list[TimelineEvent] = []
    incorporated = incorporation(pack)
    if incorporated is not None:
        on, source = incorporated
        facts.append(TimelineEvent(on=on, kind="incorporated", label=f"Incorporated, per {source}",
                                   doc_id=PACK_DOC, page=1))
    if pack.manifest is not None:
        facts.append(TimelineEvent(on=pack.manifest.application_date, kind="application",
                                   label=f"Application {pack.manifest.application_id} submitted",
                                   doc_id=PACK_DOC, page=1))
    facts += [
        TimelineEvent(on=f.financial_year_end, kind="financial_year_end",
                      label=f"Financial year ends (revenue SGD {f.revenue})", doc_id=f.doc_id, page=f.page)
        for f in pack.financial_statements
    ]
    facts += [
        TimelineEvent(on=s.period_start, kind="bank_statement",
                      label=f"Bank statement {s.period_start} to {s.period_end}", doc_id=s.doc_id, page=s.page)
        for s in pack.bank_statements
    ]
    po_invoice = {
        po.doc_id: inv.invoice_number
        for inv in pack.invoices if (po := matching_po(inv, pack)) is not None
    }
    facts += [
        TimelineEvent(on=po.po_date, kind="purchase_order",
                      label=f"PO {po.po_number} placed by {po.buyer_name}",
                      doc_id=po.doc_id, page=po.page, ref=po_invoice.get(po.doc_id, ""))
        for po in pack.purchase_orders
    ]
    facts += [
        TimelineEvent(on=b.shipped_date, kind="shipped",
                      label=f"B/L {b.bl_number}: shipped on {b.vessel}",
                      doc_id=b.doc_id, page=b.page, ref=b.invoice_reference)
        for b in pack.bills_of_lading
    ]
    facts += [
        TimelineEvent(on=d.shipped_date, kind="shipped",
                      label=f"Delivery order {d.do_number}: delivered to {d.consignee_name}",
                      doc_id=d.doc_id, page=d.page, ref=d.invoice_reference)
        for d in pack.delivery_orders
    ]
    facts += [
        TimelineEvent(on=i.invoice_date, kind="invoice",
                      label=f"Invoice {i.invoice_number} to {i.buyer_name}, SGD {i.total}",
                      doc_id=i.doc_id, page=i.page, ref=i.invoice_number)
        for i in pack.invoices
    ]
    numbers = [i.invoice_number for i in pack.invoices]
    facts += [
        TimelineEvent(on=t.date, kind="payment", label=f"Received SGD {t.credit}: {t.description}",
                      doc_id=s.doc_id, page=s.page, ref=ref)
        for s in pack.bank_statements
        for t in s.transactions
        if t.credit is not None
        and (ref := next((n for n in numbers if n.upper() in t.description.upper()), None))
    ]
    return facts


def check_temp_before_incorporation(pack: ExtractedPack) -> list[Evidence]:
    """No document can describe trading by a company before it existed."""
    known = incorporation(pack)
    if known is None:
        return []
    incorporated, source = known
    return [
        _ev(
            "TEMP_BEFORE_INCORPORATION", "fraud_signal", "high", f.doc_id, f.page,
            f"{f.label} is dated {f.on}, before the company was incorporated on "
            f"{incorporated} according to {source}.",
            expected=f"on or after {incorporated}",
            observed=str(f.on),
        )
        for f in dated_facts(pack)
        # a payment is already covered by its statement's period
        if f.kind not in ("incorporated", "application", "payment") and breaks(incorporated, f.on)
    ]


def _out_of_order(po_date: date, step: str, on: date) -> bool:
    """Whether *step* dated *on* breaks the ordering policy against its
    purchase order (gldb.ordering, which the AI team's check reads too)."""
    dates = TradeDates(purchase_order=po_date, shipped=on if step == "shipped" else None,
                       invoice=on if step == "invoice" else po_date, payment=None, incorporated=None)
    return any(v.rule == "TEMP_TRADE_SEQUENCE" and v.later == step for v in violations(dates))


def check_temp_trade_sequence(pack: ExtractedPack) -> list[Evidence]:
    """A trade runs order -> shipment, and order -> invoice. An invoice or a
    shipment dated before its purchase order suggests the order was raised
    afterwards to support the sale on paper (a retrospective PO)."""
    results: list[Evidence] = []
    invoices = {i.invoice_number: i for i in pack.invoices}
    for inv in pack.invoices:
        po = matching_po(inv, pack)
        if po is not None and _out_of_order(po.po_date, "invoice", inv.invoice_date):
            results.append(_ev(
                "TEMP_TRADE_SEQUENCE", "fraud_signal", "medium", inv.doc_id, inv.page,
                f"Invoice {inv.invoice_number} is dated {inv.invoice_date}, before its "
                f"purchase order {po.po_number} ({po.po_date}).",
                expected=f"on or after {po.po_date}",
                observed=str(inv.invoice_date),
            ))
    for shipment in pack.shipments():
        inv = invoices.get(shipment.invoice_reference)
        po = matching_po(inv, pack) if inv is not None else None
        if po is not None and _out_of_order(po.po_date, "shipped", shipment.shipped_date):
            results.append(_ev(
                "TEMP_TRADE_SEQUENCE", "fraud_signal", "medium", shipment.doc_id, shipment.page,
                f"{shipment.title} says goods were {shipment.verb} on {shipment.shipped_date}, "
                f"before purchase order {po.po_number} ({po.po_date}) was placed.",
                expected=f"on or after {po.po_date}",
                observed=str(shipment.shipped_date),
            ))
    return results


# ---------------------------------------------------------------------------
# E. RULE GROUPS AND RUNNER
# ---------------------------------------------------------------------------

CheckFn = Callable[[ExtractedPack], list[Evidence]]


@dataclass(frozen=True)
class RuleGroup:
    """A group of checks that emits Evidence only. Rule groups never score:
    gldb.scoring turns their fraud_signal Evidence into the risk score."""

    name: str
    description: str
    checks: list[CheckFn]


#: Rule groups in report order. Evidence IDs follow this order.
RULE_GROUPS: list[RuleGroup] = [
    RuleGroup(
        "Document Integrity",
        "Arithmetic, running balances and PDF metadata within each document.",
        [
            check_inv_arith,
            check_bank_running_bal,
            check_bank_continuity,
            check_fs_balance,
            check_meta_mod_after_create,
            check_meta_producer_mismatch,
        ],
    ),
    RuleGroup(
        "Forensics",
        "Hidden text, figures edited or pasted over the original, re-saved PDFs, and "
        "instructions aimed at AI models, found in the raw file, by comparing it with "
        "what OCR sees, and by a Bedrock guardrail.",
        [
            check_forensic_hidden_text,
            check_forensic_mixed_font,
            check_forensic_incremental_update,
            check_forensic_ocr_mismatch,
            check_prompt_injection,
        ],
    ),
    RuleGroup(
        "Cross-Document",
        "Consistency of identity, amounts and quantities across invoices, purchase orders, "
        "bills of lading, bank statements, financial statements and the ACRA profile, and money "
        "that only passes through the account.",
        [
            check_xdoc_identity,
            check_xdoc_invoice_bank,
            check_xdoc_po_invoice,
            check_xdoc_bl_invoice,
            check_fs_acra,
            check_xdoc_bank_cash,
            check_xdoc_bank_debt,
            check_xdoc_bank_revenue,
            check_bank_pass_through,
            check_acra_entity,
            check_acra_signatories,
            check_acra_persons,
        ],
    ),
    RuleGroup(
        "Registry & Screening",
        "Companies and people checked against ACRA's and IRAS's own records (fetched, not uploaded) "
        "the sanctions list and FATF's high-risk countries (versioned reference data).",
        [check_official_records, check_official_copies, check_registry_entities, check_registry_signatories,
         check_sanctions, check_high_risk_countries],
    ),
    RuleGroup(
        "Timeline",
        "Dates across documents: trading before the company existed, and orders, "
        "shipments and invoices out of sequence.",
        [check_temp_before_incorporation, check_temp_trade_sequence],
    ),
    RuleGroup(
        "Compliance & Completeness",
        "Required documents, bank coverage, and FATCA/CRS form validity.",
        [
            check_bank_coverage,
            check_checklist_missing_doc,
            check_form_ent_mandatory,
            check_form_ent_mailing,
            check_form_ent_branch,
            check_form_ent_us_status,
            check_form_ent_tin_or_reason,
            check_form_ent_type_selection,
            check_form_ent_declaration,
            check_form_template_match,
            check_form_ent_name_match,
            check_form_ent_address_match,
            check_form_ent_incorporation,
            check_form_ent_sg_tin,
            check_form_ent_start_up,
            check_form_ent_signatory,
        ],
    ),
]


def _run_rule_group(group: RuleGroup, pack: ExtractedPack) -> tuple[list[Evidence], RuleGroupReport]:
    """Run one rule group's checks. A crashing check emits SYSTEM_CHECK_ERROR
    (compliance, medium, 1.0) rather than propagating the exception."""
    start = perf_counter()
    found: list[Evidence] = []
    for check_fn in group.checks:
        try:
            found.extend(check_fn(pack))
        except Exception as exc:  # noqa: BLE001
            found.append(_ev(
                "SYSTEM_CHECK_ERROR", "compliance", "medium",
                PACK_DOC, 1,
                f"{check_fn.__name__} failed to run; manual verification required. "
                f"Error: {exc}",
            ))
    for ev in found:
        ev.rule_group = group.name
    report = RuleGroupReport(
        name=group.name,
        description=group.description,
        checks_run=len(group.checks),
        findings=sum(ev.severity != "info" for ev in found),
        duration_ms=round((perf_counter() - start) * 1000),
    )
    return found, report


def run_rule_checks(
    pack: ExtractedPack,
    extra_evidence: list[Evidence] | None = None,
) -> tuple[list[Evidence], list[RuleGroupReport]]:
    """Run every rule group in parallel and merge their Evidence.

    Results are merged in RULE_GROUPS order (not completion order), so the
    output is deterministic. Evidence IDs are assigned E01…En with no gaps.
    """
    with ThreadPoolExecutor(max_workers=len(RULE_GROUPS)) as pool:
        outcomes = list(pool.map(lambda group: _run_rule_group(group, pack), RULE_GROUPS))

    collected = [ev for found, _ in outcomes for ev in found]
    collected.extend(extra_evidence or [])
    for i, ev in enumerate(collected, start=1):
        ev.evidence_id = f"E{i:02d}"
    return collected, [report for _, report in outcomes]
