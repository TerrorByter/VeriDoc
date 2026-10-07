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
- Checks are grouped into Agents (section E); run_agents() runs them.
- FORM_DOC_TYPES docs are skipped for META_MOD_AFTER_CREATE and
  META_PRODUCER_MISMATCH.
"""

from __future__ import annotations

import re
import string
from calendar import monthrange
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter
from typing import Callable

from gldb.config import (
    BANK_RECENCY_DAYS,
    CHECKLIST,
    HIDDEN_QUOTE_MAX_CHARS,
    GST_RATE,
    MATCH_WINDOW_DAYS,
    META_MOD_GAP_HOURS,
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
from gldb.intake import read_text
from gldb.ordering import TradeDates, breaks, violations
from gldb.privacy import mask_id
from gldb.reference import registry, watchlist
from gldb.screening import screen, screening_key, similarity
from gldb.schemas import (
    AgentReport,
    ApplicationManifest,
    DocMeta,
    EntitySelfCert,
    Evidence,
    EvidenceCategory,
    ExtractedPack,
    Invoice,
    PurchaseOrder,
    Severity,
    TimelineEvent,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FORM_DOC_TYPES = {"fatca_crs_entity", "fatca_crs_individual"}

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
    confidence: float,
    explanation: str,
    *,
    quote: str | None = None,
    expected: str | None = None,
    observed: str | None = None,
) -> Evidence:
    return Evidence(
        evidence_id="",   # assigned later by run_agents
        check_id=check_id,
        category=category,
        severity=severity,
        document=document,
        page=page,
        quote=quote,
        expected=expected,
        observed=observed,
        method="deterministic",
        confidence=confidence,
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
        1.0,
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
                    "INV_ARITH", "fraud_signal", "high", doc, inv.page, 1.0,
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
                "INV_ARITH", "fraud_signal", "high", doc, inv.page, 1.0,
                f"Invoice subtotal SGD {inv.subtotal} does not equal sum of "
                f"line amounts SGD {expected_subtotal}.",
                expected=f"SGD {expected_subtotal}",
                observed=f"SGD {inv.subtotal}",
                quote=inv.source_quotes.get("Subtotal"),
            ))

        # c) gst == subtotal × GST_RATE
        expected_gst = money(inv.subtotal * GST_RATE)
        if abs(expected_gst - inv.gst) > TOLERANCE_SGD:
            results.append(_ev(
                "INV_ARITH", "fraud_signal", "high", doc, inv.page, 1.0,
                f"GST SGD {inv.gst} does not equal subtotal SGD {inv.subtotal} "
                f"× {int(GST_RATE * 100)}% = SGD {expected_gst}.",
                expected=f"SGD {expected_gst}",
                observed=f"SGD {inv.gst}",
                quote=inv.source_quotes.get("GST (9%)"),
            ))

        # d) total == subtotal + gst
        expected_total = money(inv.subtotal + inv.gst)
        if abs(expected_total - inv.total) > TOLERANCE_SGD:
            results.append(_ev(
                "INV_ARITH", "fraud_signal", "high", doc, inv.page, 1.0,
                f"Invoice total SGD {inv.total} does not equal subtotal "
                f"SGD {inv.subtotal} + GST SGD {inv.gst} = SGD {expected_total}.",
                expected=f"SGD {expected_total}",
                observed=f"SGD {inv.total}",
                quote=inv.source_quotes.get("Total"),
            ))

    return results


# 2. BANK_RUNNING_BAL ----------------------------------------------------

def check_bank_running_bal(pack: ExtractedPack) -> list[Evidence]:
    """Walk each statement's transactions and verify running balance."""
    results: list[Evidence] = []
    for stmt in pack.bank_statements:
        doc = stmt.doc_id
        running = stmt.opening_balance

        for txn in stmt.transactions:
            if txn.credit is not None:
                running = money(running + txn.credit)
            if txn.debit is not None:
                running = money(running - txn.debit)

            if abs(running - txn.balance) > TOLERANCE_SGD:
                quote = stmt.source_quotes.get(txn.description, txn.description)
                results.append(_ev(
                    "BANK_RUNNING_BAL", "fraud_signal", "high", doc, stmt.page, 1.0,
                    f"Running balance after transaction '{txn.description}' on "
                    f"{txn.date} should be SGD {running} but printed balance is "
                    f"SGD {txn.balance}.",
                    expected=f"SGD {running}",
                    observed=f"SGD {txn.balance}",
                    quote=quote,
                ))

        # Compare computed final balance against stated closing balance
        if abs(running - stmt.closing_balance) > TOLERANCE_SGD:
            results.append(_ev(
                "BANK_RUNNING_BAL", "fraud_signal", "high", doc, stmt.page, 1.0,
                f"Closing balance SGD {stmt.closing_balance} is inconsistent with "
                f"the running total SGD {running}.",
                expected=f"SGD {running}",
                observed=f"SGD {stmt.closing_balance}",
                quote=stmt.source_quotes.get("Closing Balance"),
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
                nxt.doc_id, nxt.page, 1.0,
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
                "FS_BALANCE", "fraud_signal", "high", fs.doc_id, fs.page, 1.0,
                f"Balance sheet does not balance: total assets SGD {fs.total_assets} "
                f"≠ liabilities SGD {fs.total_liabilities} + equity "
                f"SGD {fs.total_equity} = SGD {expected}.",
                expected=f"SGD {expected}",
                observed=f"SGD {fs.total_assets}",
            ))
    return results


# 5. XDOC_IDENTITY -------------------------------------------------------

def check_xdoc_identity(pack: ExtractedPack) -> list[Evidence]:
    """Cross-document identity checks using the FS summary as reference."""
    results: list[Evidence] = []
    if not pack.financial_statements:
        return results

    fs = pack.financial_statements[0]
    ref_uen = fs.uen
    ref_name_norm = normalise_name(fs.company_name)

    # UEN check on invoices (high, 1.0)
    for inv in pack.invoices:
        if inv.seller_uen != ref_uen:
            results.append(_ev(
                "XDOC_IDENTITY", "fraud_signal", "high", inv.doc_id, inv.page, 1.0,
                f"Invoice seller UEN '{inv.seller_uen}' does not match the "
                f"FS UEN '{ref_uen}'.",
                expected=ref_uen,
                observed=inv.seller_uen,
            ))

    # Name check on invoices (medium, 0.9)
    for inv in pack.invoices:
        inv_name_norm = normalise_name(inv.seller_name)
        if inv_name_norm != ref_name_norm:
            results.append(_ev(
                "XDOC_IDENTITY", "fraud_signal", "medium", inv.doc_id, inv.page, 0.9,
                f"Invoice seller name '{inv.seller_name}' (normalised: "
                f"'{inv_name_norm}') does not match FS company name "
                f"'{fs.company_name}' (normalised: '{ref_name_norm}').",
                expected=ref_name_norm,
                observed=inv_name_norm,
            ))

    # Name check on bank statements (medium, 0.9)
    for stmt in pack.bank_statements:
        holder_norm = normalise_name(stmt.account_holder)
        if holder_norm != ref_name_norm:
            results.append(_ev(
                "XDOC_IDENTITY", "fraud_signal", "medium", stmt.doc_id, stmt.page, 0.9,
                f"Bank account holder '{stmt.account_holder}' (normalised: "
                f"'{holder_norm}') does not match FS company name "
                f"'{fs.company_name}' (normalised: '{ref_name_norm}').",
                expected=ref_name_norm,
                observed=holder_norm,
            ))

    # Purchase orders: seller must be the applicant (high 1.0 / medium 0.9)
    for po in pack.purchase_orders:
        if po.seller_uen != ref_uen:
            results.append(_ev(
                "XDOC_IDENTITY", "fraud_signal", "high", po.doc_id, po.page, 1.0,
                f"Purchase order seller UEN '{po.seller_uen}' does not match the "
                f"FS UEN '{ref_uen}'.",
                expected=ref_uen,
                observed=po.seller_uen,
            ))
        po_name_norm = normalise_name(po.seller_name)
        if po_name_norm != ref_name_norm:
            results.append(_ev(
                "XDOC_IDENTITY", "fraud_signal", "medium", po.doc_id, po.page, 0.9,
                f"Purchase order seller name '{po.seller_name}' does not match FS "
                f"company name '{fs.company_name}'.",
                expected=ref_name_norm,
                observed=po_name_norm,
            ))

    return results


# 6. XDOC_INVOICE_BANK ---------------------------------------------------

def check_xdoc_invoice_bank(pack: ExtractedPack) -> list[Evidence]:
    """For each invoice, find a matching bank credit within MATCH_WINDOW_DAYS."""
    results: list[Evidence] = []

    # Collect all credits across all statements, each usable at most once.
    # Tuple: (stmt_doc_id, transaction)
    available_credits: list[tuple[str, object]] = []
    for stmt in pack.bank_statements:
        for txn in stmt.transactions:
            if txn.credit is not None:
                available_credits.append((stmt.doc_id, txn))

    used: set[int] = set()   # indices into available_credits

    for inv in pack.invoices:
        window_end = inv.invoice_date + timedelta(days=MATCH_WINDOW_DAYS)

        # Gather candidates: credits in date window
        candidates = [
            (idx, doc_id, txn)
            for idx, (doc_id, txn) in enumerate(available_credits)
            if idx not in used
            and inv.invoice_date <= txn.date <= window_end
        ]

        # Find exact match (within tolerance)
        exact_idx: int | None = None
        for idx, doc_id, txn in candidates:
            if abs(txn.credit - inv.total) <= TOLERANCE_SGD:
                exact_idx = idx
                break

        if exact_idx is not None:
            _, txn = available_credits[exact_idx]
            used.add(exact_idx)
            # Check whether it's truly exact or just within tolerance
            if txn.credit != inv.total:
                results.append(_ev(
                    "XDOC_INVOICE_BANK", "fraud_signal", "info",
                    inv.doc_id, inv.page, 1.0,
                    f"Invoice {inv.invoice_number} total SGD {inv.total}: matching "
                    f"bank credit SGD {txn.credit} on {txn.date} differs by "
                    f"SGD {abs(txn.credit - inv.total):.2f} (within tolerance — "
                    f"likely a partial payment or rounding).",
                    expected=f"SGD {inv.total}",
                    observed=f"SGD {txn.credit} on {txn.date}",
                ))
        else:
            # Find closest credit by amount (across all statements, not just window)
            all_unused = [
                (idx, doc_id, txn)
                for idx, (doc_id, txn) in enumerate(available_credits)
                if idx not in used
            ]
            closest_str = "none"
            if all_unused:
                best = min(all_unused, key=lambda x: abs(x[2].credit - inv.total))
                closest_str = f"SGD {best[2].credit} on {best[2].date}"

            results.append(_ev(
                "XDOC_INVOICE_BANK", "fraud_signal", "high",
                inv.doc_id, inv.page, 0.95,
                f"No bank credit matching invoice {inv.invoice_number} "
                f"(SGD {inv.total}) found within {MATCH_WINDOW_DAYS} days "
                f"after {inv.invoice_date}.",
                expected=(
                    f"credit of SGD {inv.total} between {inv.invoice_date} "
                    f"and {window_end}"
                ),
                observed=closest_str,
            ))

    return results


# 6b. XDOC_PO_INVOICE ----------------------------------------------------

def matching_po(inv: Invoice, pack: ExtractedPack) -> PurchaseOrder | None:
    """The purchase order an invoice relates to: from the same buyer, with
    the closest total. None when that buyer placed no order."""
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
                "XDOC_PO_INVOICE", "fraud_signal", "medium", inv.doc_id, inv.page, 0.7,
                f"Invoice {inv.invoice_number} bills '{inv.buyer_name}' but none of the "
                f"{len(pack.purchase_orders)} purchase orders in the pack is from that buyer.",
                expected=f"purchase order from {inv.buyer_name}",
                observed="none",
            ))
        elif inv.total - po.total > TOLERANCE_SGD:
            results.append(_ev(
                "XDOC_PO_INVOICE", "fraud_signal", "high", inv.doc_id, inv.page, 0.95,
                f"Invoice {inv.invoice_number} total SGD {inv.total} exceeds purchase "
                f"order {po.po_number} total SGD {po.total} by SGD {inv.total - po.total}.",
                quote=inv.source_quotes.get("Total"),
                expected=f"at most SGD {po.total}",
                observed=f"SGD {inv.total}",
            ))

    return results


def check_xdoc_bl_invoice(pack: ExtractedPack) -> list[Evidence]:
    """Invoiced quantities should not exceed what the bill of lading shows
    was shipped: billing for more goods than were shipped is the classic
    over-invoicing red flag in trade-based money laundering.

    Only runs when the pack contains bills of lading, which link to an invoice
    by their invoice reference. Invoice lines absent from the bill of lading
    (services, which are never shipped) are not flagged.
    """
    results: list[Evidence] = []
    if not pack.bills_of_lading:
        return results

    for inv in pack.invoices:
        bls = [b for b in pack.bills_of_lading if b.invoice_reference == inv.invoice_number]
        if not bls:
            results.append(_ev(
                "XDOC_BL_INVOICE", "fraud_signal", "medium", inv.doc_id, inv.page, 0.7,
                f"No bill of lading in the pack refers to invoice {inv.invoice_number}, "
                "so the goods billed cannot be traced to a shipment.",
                expected=f"bill of lading referring to {inv.invoice_number}",
                observed="none",
            ))
            continue
        shipped: dict[str, tuple[int, str]] = {}
        for bl in bls:
            for g in bl.goods:
                key = normalise_name(g.description)
                total, _ = shipped.get(key, (0, ""))
                shipped[key] = (total + g.quantity, bl.bl_number)
        for item in inv.line_items:
            match = shipped.get(normalise_name(item.description))
            if match and item.quantity > match[0]:
                qty, bl_number = match
                results.append(_ev(
                    "XDOC_BL_INVOICE", "fraud_signal", "high", inv.doc_id, inv.page, 0.95,
                    f"Invoice {inv.invoice_number} bills {item.quantity} x '{item.description}' "
                    f"but bill of lading {bl_number} shows only {qty} shipped.",
                    expected=f"at most {qty} shipped ({bl_number})",
                    observed=f"{item.quantity} invoiced",
                ))
    return results


# 7. META_MOD_AFTER_CREATE -----------------------------------------------

def check_meta_mod_after_create(pack: ExtractedPack) -> list[Evidence]:
    """Flag docs where mod_date - creation_date > META_MOD_GAP_HOURS."""
    results: list[Evidence] = []
    for doc in pack.docs:
        if doc.doc_type in FORM_DOC_TYPES:
            continue
        if doc.creation_date is None or doc.mod_date is None:
            continue
        delta_hours = (doc.mod_date - doc.creation_date).total_seconds() / 3600
        if delta_hours > META_MOD_GAP_HOURS:
            results.append(_ev(
                "META_MOD_AFTER_CREATE", "fraud_signal", "high",
                doc.doc_id, 1, 0.7,
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

def check_meta_producer_mismatch(pack: ExtractedPack) -> list[Evidence]:
    """Within one document type, any doc whose producer differs from that
    type's majority triggers a flag. Different types come from different
    issuers (the bank, IRAS, the applicant's accounts software), so their
    producers are never compared with each other."""
    from collections import Counter

    results: list[Evidence] = []
    by_type: dict[str, list[DocMeta]] = {}
    for d in pack.docs:
        if d.doc_type not in FORM_DOC_TYPES and d.producer:
            by_type.setdefault(d.doc_type, []).append(d)

    for docs in by_type.values():
        majority_producer, _ = Counter(d.producer for d in docs).most_common(1)[0]
        for doc in docs:
            if doc.producer != majority_producer:
                results.append(_ev(
                    "META_PRODUCER_MISMATCH", "fraud_signal", "medium",
                    doc.doc_id, 1, 0.5,
                    f"PDF producer '{doc.producer}' differs from '{majority_producer}', "
                    f"which made the other {doc.doc_type.replace('_', ' ')} documents in the pack. "
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
    """Statements must cover REQUIRED_BANK_MONTHS consecutive months;
    latest must not be stale relative to application_date."""
    results: list[Evidence] = []
    if not pack.bank_statements:
        results.append(_ev(
            "BANK_COVERAGE", "completeness", "medium",
            PACK_DOC, 1, 1.0,
            "No bank statements found in the pack.",
            expected=f"{REQUIRED_BANK_MONTHS} months of statements",
            observed="0 statements",
        ))
        return results

    stmts = sorted(pack.bank_statements, key=lambda s: s.period_start)
    latest = stmts[-1]

    # Build set of (year, month) present
    present: set[tuple[int, int]] = set()
    for s in stmts:
        present.add((s.period_start.year, s.period_start.month))

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
            PACK_DOC, 1, 1.0,
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
                latest.doc_id, latest.page, 1.0,
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
                    PACK_DOC, 1, 1.0,
                    f"Required document '{label}' is missing from the pack.",
                    expected="fatca_crs_entity document present",
                    observed="not found",
                ))

        elif item_id == "BANK_6M":
            if "bank_statement" not in doc_types_present:
                results.append(_ev(
                    "CHECKLIST_MISSING_DOC", "completeness", "high",
                    PACK_DOC, 1, 1.0,
                    f"Required document '{label}' is missing from the pack.",
                    expected="at least one bank_statement document present",
                    observed="not found",
                ))

    return results


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
) -> Evidence:
    return _ev(
        check_id, "compliance", severity, cert.doc_id, 1, 1.0,
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
                    observed=sig.name,
                ))
    return results


# ---------------------------------------------------------------------------
# F. FORENSICS (inspect the raw file, see gldb.forensics)
# ---------------------------------------------------------------------------

def _forensic_docs(pack: ExtractedPack):
    """(doc_id, path) for files we can inspect. Official form templates are
    skipped: they legitimately use white headings and mixed fonts."""
    for doc in pack.docs:
        path = pack.paths.get(doc.doc_id)
        if path is not None and doc.doc_type not in FORM_DOC_TYPES:
            yield doc.doc_id, path


def check_forensic_hidden_text(pack: ExtractedPack) -> list[Evidence]:
    return [
        _ev(
            "FORENSIC_HIDDEN_TEXT", "fraud_signal", "high", doc_id, run.page, 0.9,
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
            "FORENSIC_MIXED_FONT", "fraud_signal", "medium", doc_id, token.page, 0.7,
            f"'{token.text}' in {doc_id} page {token.page} mixes fonts or sizes within "
            "one token, which can mean a figure was edited on top of the original.",
            quote=token.text,
        )
        for doc_id, path in _forensic_docs(pack)
        for token in mixed_font_tokens(path)
    ]


def check_forensic_incremental_update(pack: ExtractedPack) -> list[Evidence]:
    results = []
    for doc_id, path in _forensic_docs(pack):
        revisions = incremental_updates(path)
        if revisions:
            results.append(_ev(
                "FORENSIC_INCREMENTAL_UPDATE", "fraud_signal", "medium", doc_id, 1, 0.6,
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
            "FORENSIC_OCR_SKIPPED", "fraud_signal", "info", PACK_DOC, 1, 1.0,
            "The text layer vs OCR comparison did not run because OCR is turned off. "
            "Figures covered or pasted over as images would not be detected.",
        )]
    results = []
    for doc_id, path in _forensic_docs(pack):
        seen = pack.ocr.get(doc_id)
        if seen is None:
            continue
        for m in amount_mismatches(read_text(path, visible_only=True), seen):
            results.append(_ev(
                "FORENSIC_OCR_MISMATCH", "fraud_signal", "medium", doc_id, m.page, 0.6,
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
            "PROMPT_SCREEN_SKIPPED", "fraud_signal", "info", PACK_DOC, 1, 1.0,
            "Document text was not screened for prompt attacks (PROMPT_SCREEN_ENABLED is off).",
        )]
    return [
        _ev(
            "PROMPT_INJECTION", "fraud_signal", "high", doc_id, hit.page, 0.9,
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
# G. REGISTRY & SCREENING (versioned reference data, see gldb.reference)
# ---------------------------------------------------------------------------

def _registered_entities(pack: ExtractedPack) -> list[tuple[str, int, str, str, str]]:
    """(doc_id, page, role, UEN, name) for every company identified by UEN."""
    found = []
    if pack.manifest is not None:
        found.append((PACK_DOC, 1, "applicant", pack.manifest.uen,
                      pack.manifest.company_name))
    found += [(i.doc_id, i.page, "seller", i.seller_uen, i.seller_name) for i in pack.invoices]
    found += [(p.doc_id, p.page, "seller", p.seller_uen, p.seller_name) for p in pack.purchase_orders]
    found += [(f.doc_id, f.page, "company", f.uen, f.company_name) for f in pack.financial_statements]
    return found


def check_registry_entities(pack: ExtractedPack) -> list[Evidence]:
    """Each UEN should be registered, under the name used, and live."""
    reg = registry()
    results = []
    for doc_id, page, role, uen, name in _registered_entities(pack):
        entity = reg.entities.get(uen)
        where = f"the {role} on {doc_id}" if doc_id != PACK_DOC else "the applicant"
        if entity is None:
            results.append(_ev(
                "REGISTRY_ENTITY_NOT_FOUND", "fraud_signal", "high", doc_id, page, 0.95,
                f"UEN {uen} given for {where} ('{name}') is not in the company register "
                f"({reg.version}, as of {reg.as_of}).",
                expected="a registered UEN", observed=f"UEN {uen}",
            ))
            continue
        if similarity(name, entity.name) < SCREENING_MATCH_THRESHOLD:
            results.append(_ev(
                "REGISTRY_NAME_MISMATCH", "fraud_signal", "high", doc_id, page, 0.9,
                f"UEN {uen} is registered to '{entity.name}', but {where} names "
                f"'{name}' ({reg.version}).",
                expected=entity.name, observed=name,
            ))
        if entity.status != "Live":
            results.append(_ev(
                "REGISTRY_ENTITY_INACTIVE", "fraud_signal", "high", doc_id, page, 0.95,
                f"'{entity.name}' (UEN {uen}) is '{entity.status}' in the company register "
                f"({reg.version}), so it cannot trade.",
                expected="Live", observed=entity.status,
            ))
    return results


def check_registry_signatories(pack: ExtractedPack) -> list[Evidence]:
    """Authorised signatories should be registered officers of the applicant."""
    if pack.manifest is None:
        return []
    reg = registry()
    entity = reg.entities.get(pack.manifest.uen)
    if entity is None:
        return []  # REGISTRY_ENTITY_NOT_FOUND already reports it
    return [
        _ev(
            "REGISTRY_SIGNATORY_NOT_OFFICER", "fraud_signal", "medium",
            PACK_DOC, 1, 0.8,
            f"Authorised signatory '{person}' is not a registered officer of "
            f"'{entity.name}' ({reg.version}).",
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
    mentions += [(p.buyer_name, p.doc_id, p.page, "buyer") for p in pack.purchase_orders]
    for b in pack.bills_of_lading:
        mentions += [(b.shipper_name, b.doc_id, b.page, "shipper"),
                     (b.consignee_name, b.doc_id, b.page, "consignee")]
    names: dict[str, tuple[str, str, int, str]] = {}
    for name, doc_id, page, role in mentions:
        names.setdefault(screening_key(name), (name, doc_id, page, role))
    return names


def check_sanctions(pack: ExtractedPack) -> list[Evidence]:
    """Screen the applicant, its people and its counterparties against the
    sanctions list. A hit is a possible match for a person to dispose of."""
    wl = watchlist()
    results = []
    for name, doc_id, page, role in _screened_names(pack).values():
        for entry, score in screen(name):
            results.append(_ev(
                "SANCTIONS_MATCH", "fraud_signal", "high", doc_id, page, round(score, 2),
                f"The {role} '{name}' resembles '{entry.name}' on {entry.list} "
                f"({entry.reference}), similarity {score:.2f}. Sanctions list {wl.version}, "
                f"as of {wl.as_of}. Confirm or dismiss the match before proceeding.",
                quote=name,
                expected="no sanctions match",
                observed=f"'{name}' ~ '{entry.name}' ({entry.list} {entry.reference})",
            ))
    return results


# ---------------------------------------------------------------------------
# H. TEMPORAL CONSISTENCY
# Evidence "observed" holds the date found wrong, so gldb.timeline can mark
# the exact event a finding is about.
# ---------------------------------------------------------------------------

def incorporation(manifest: ApplicationManifest) -> tuple[date, str]:
    """The applicant's incorporation date and its source: the company
    register when it lists the UEN, otherwise the application itself."""
    reg = registry()
    entity = reg.entities.get(manifest.uen)
    if entity is not None:
        return entity.incorporated, f"the company register ({reg.version})"
    return manifest.date_of_incorporation, "the application"


def dated_facts(pack: ExtractedPack) -> list[TimelineEvent]:
    """Every dated fact the pack states, unsorted. Trade facts carry the
    invoice they belong to as *ref*."""
    facts: list[TimelineEvent] = []
    if pack.manifest is not None:
        on, source = incorporation(pack.manifest)
        facts.append(TimelineEvent(on=on, kind="incorporated", label=f"Incorporated, per {source}",
                                   doc_id=PACK_DOC, page=1))
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
    if pack.manifest is None:
        return []
    incorporated, source = incorporation(pack.manifest)
    return [
        _ev(
            "TEMP_BEFORE_INCORPORATION", "fraud_signal", "high", f.doc_id, f.page, 0.95,
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
                "TEMP_TRADE_SEQUENCE", "fraud_signal", "medium", inv.doc_id, inv.page, 0.9,
                f"Invoice {inv.invoice_number} is dated {inv.invoice_date}, before its "
                f"purchase order {po.po_number} ({po.po_date}).",
                expected=f"on or after {po.po_date}",
                observed=str(inv.invoice_date),
            ))
    for bl in pack.bills_of_lading:
        inv = invoices.get(bl.invoice_reference)
        po = matching_po(inv, pack) if inv is not None else None
        if po is not None and _out_of_order(po.po_date, "shipped", bl.shipped_date):
            results.append(_ev(
                "TEMP_TRADE_SEQUENCE", "fraud_signal", "medium", bl.doc_id, bl.page, 0.9,
                f"Bill of lading {bl.bl_number} says goods were shipped on {bl.shipped_date}, "
                f"before purchase order {po.po_number} ({po.po_date}) was placed.",
                expected=f"on or after {po.po_date}",
                observed=str(bl.shipped_date),
            ))
    return results


# ---------------------------------------------------------------------------
# E. AGENTS AND RUNNER
# ---------------------------------------------------------------------------

CheckFn = Callable[[ExtractedPack], list[Evidence]]


@dataclass(frozen=True)
class Agent:
    """A specialist that runs a group of checks and emits Evidence only.
    Agents never score: gldb.scoring turns their fraud_signal Evidence into
    the risk score."""

    name: str
    description: str
    checks: list[CheckFn]


#: Agents in report order. Evidence IDs follow this order.
AGENTS: list[Agent] = [
    Agent(
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
    Agent(
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
    Agent(
        "Cross-Document",
        "Consistency of identity, amounts and quantities across invoices, purchase orders, "
        "bills of lading, bank and statements.",
        [check_xdoc_identity, check_xdoc_invoice_bank, check_xdoc_po_invoice, check_xdoc_bl_invoice],
    ),
    Agent(
        "Registry & Screening",
        "Companies and people checked against the company register and the sanctions list "
        "(versioned reference data).",
        [check_registry_entities, check_registry_signatories, check_sanctions],
    ),
    Agent(
        "Timeline",
        "Dates across documents: trading before the company existed, and orders, "
        "shipments and invoices out of sequence.",
        [check_temp_before_incorporation, check_temp_trade_sequence],
    ),
    Agent(
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


def _run_agent(agent: Agent, pack: ExtractedPack) -> tuple[list[Evidence], AgentReport]:
    """Run one agent's checks. A crashing check emits SYSTEM_CHECK_ERROR
    (compliance, medium, 1.0) rather than propagating the exception."""
    start = perf_counter()
    found: list[Evidence] = []
    for check_fn in agent.checks:
        try:
            found.extend(check_fn(pack))
        except Exception as exc:  # noqa: BLE001
            found.append(_ev(
                "SYSTEM_CHECK_ERROR", "compliance", "medium",
                PACK_DOC, 1, 1.0,
                f"{check_fn.__name__} failed to run; manual verification required. "
                f"Error: {exc}",
            ))
    for ev in found:
        ev.agent = agent.name
    report = AgentReport(
        name=agent.name,
        description=agent.description,
        checks_run=len(agent.checks),
        findings=sum(ev.severity != "info" for ev in found),
        duration_ms=round((perf_counter() - start) * 1000),
    )
    return found, report


def run_agents(
    pack: ExtractedPack,
    extra_evidence: list[Evidence] | None = None,
) -> tuple[list[Evidence], list[AgentReport]]:
    """Run every agent in parallel and merge their Evidence.

    Results are merged in AGENTS order (not completion order), so the output
    is deterministic. Evidence IDs are assigned E01…En with no gaps.
    """
    with ThreadPoolExecutor(max_workers=len(AGENTS)) as pool:
        outcomes = list(pool.map(lambda agent: _run_agent(agent, pack), AGENTS))

    collected = [ev for found, _ in outcomes for ev in found]
    collected.extend(extra_evidence or [])
    for i, ev in enumerate(collected, start=1):
        ev.evidence_id = f"E{i:02d}"
    return collected, [report for _, report in outcomes]
