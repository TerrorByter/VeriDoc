# Authoring a held-out test set

You do not need to read any code, and you must not read the checks or prompts.
You decide, per pack, **what is planted and whether it is legitimate**. The
renderer draws the documents; it decides nothing about your labels.

## What you produce

One JSON file, the scenario sheet, plus an answer key you keep to yourself.
The sheet is the only thing you author. It looks like this (a pilot example):

```json
{
  "test_set": "heldout-v1",
  "kind": "official",
  "author": "your name or team",
  "scenarios": [
    {
      "scenario_id": "H-001",
      "label": "fraud",
      "typology": "invoice_overbilled",
      "placement": {"trade": "sale", "index": 1},
      "company": {"profile": "metal_fabrication"},
      "notes": "First sale overbilled 25% against its order and the payment."
    },
    {
      "scenario_id": "H-002",
      "label": "legitimate",
      "quirk": "agent_payment",
      "placement": {"trade": "sale", "index": 2},
      "company": {"profile": "water_treatment"},
      "notes": "A payment agent settles the buyer's invoice; the application declares it."
    }
  ]
}
```

`kind` is `official` for the set you will stand behind (at least 40 packs, at
least 20 of each label) or `pilot` for a practice set.

## The fields

- **scenario_id** — a unique short id you invent. It does not appear in the pack.
- **label** — `fraud` or `legitimate`. This is the ground truth; the system
  never sees it until the key is revealed after the run.
- **typology** (fraud only) — one of the names below, in your own words if you
  like, but it must be one the renderer knows.
- **quirk** (legitimate only) — one of the innocent look-alikes below.
- **placement** — where the typology or quirk sits: `{"trade": "sale"|"purchase",
  "index": N}`, 1-based in statement order. Leave it out for pack-wide choices.
- **company.profile** — a business the renderer draws. Ten exist:
  `industrial_equipment`, `precision_electronics`, `marine_engineering`,
  `commodity_trading`, `investment_holding`, `building_supplies`,
  `metal_fabrication`, `factory_automation`, `water_treatment`,
  `cold_chain_logistics`. Each has its own customers, suppliers and goods. The
  renderer gives the company a fresh name and UEN, so a profile is a business,
  not an identity. You may optionally set `name` and `uen` yourself.
- **notes** — for the record; it goes into the key, not the pack.

## Fraudulent typologies available

- `invoice_overbilled` — an invoice charges more than its order and payment.
- `goods_not_shipped` — an invoice bills goods the shipping document does not carry.
- `order_never_issued` — an invoice names a purchase order that is not in the pack.
- `financials_inflated` — submitted financials overstate what ACRA holds.
- `statement_credit_raised` — a bank-statement credit was raised and the PDF re-saved.
- `identity_mismatch` — the forms disagree with the registry on signatory, office or a name.
- `sanctioned_shareholder` — a shareholder is on the sanctions list.
- `company_struck_off` — the company is struck off in the registry.
- `third_party_payment` — a sale is paid by a company with no part in it.
- `related_party_undisclosed` — a supplier is run by one of the applicant's own
  officers and nothing declares it.
- `round_trip` — a supplier payment comes back from that supplier as a receipt.

The last three are typologies the rule checks cannot see; they measure the AI
team and the honest gap in the rules.

## Innocent look-alikes available (legitimate packs)

- `clean` — nothing; the documents agree with each other and the registry.
- `supplier_refund` — a supplier returns money for goods returned; the credit
  has no invoice.
- `agent_payment` — a payment agent or factoring company settles the buyer's
  invoice, and the application declares it.
- `intra_group_payer` — a group company settles the buyer's invoice, and the
  application declares it.
- `disclosed_directorship` — one of the applicant's officers is a director of a
  supplier, and the application declares it.
- `split_payment` — the buyer pays the invoice in two instalments.

These are what measure false alarms. Without them, precision means nothing.

## What you then do

1. Author the sheet and **keep the answer key** (the renderer produces it, or
   you run `python -m gldb.heldout generate` yourself; the key is yours, not
   the team's).
2. Publish only the **seal** (the key's hash) and the packs. Never the key,
   before the run.
3. After the run, hand the key to whoever scores, and
   `python -m gldb.heldout score` verifies the seal and produces the report.

## What you must not do

Do not look at `gldb/checks.py`, `gldb/ai_team/`, `gldb/signal_library.py`, or
the demo packs' `ground_truth.json`. Do not choose typologies to match what the
checks are known to catch. Work from a public typology list (MAS and FATF
trade-based money-laundering indicators, the sponsor's red-flag list), not from
this codebase.
