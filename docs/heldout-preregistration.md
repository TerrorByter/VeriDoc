# Held-out test: pre-registration

**Status: frozen. The system measured is the commit tagged `eval-v1`; nothing here changes once the set is authored.**

This document records what will be measured, and how, before a single held-out
scenario exists. Its commit timestamp is the proof that the rules came first:
once the set is written, nothing here may change.

The system is frozen first. Tag the exact version, for example `git tag eval-v1`,
before anyone writes a scenario. Nothing is tuned after anyone has seen the
test set.

## 1. What is being tested

The deployed system, scored on packs it was not built against. The demo packs
(`data/packs/`) were written knowing the checks, so they show only that the
pipeline catches what was planted. This set is written by someone who did not
build the checks, from public typologies, and the system never sees its labels
until after the run.

## 2. Sample size

- **Recommended: 60 packs** — 30 fraudulent, 30 legitimate.
- **Minimum: 40 packs** — 20 fraudulent, 20 legitimate.
- Fraudulent packs: roughly six typologies, five packs each, chosen by the
  author from those the renderer draws (the author guide lists them),
  including the two no rule looks for (`related_party_undisclosed`,
  `round_trip`). The renderer cannot draw a typology it does not know, so no
  fraud in the set is new to the system as a whole.
- Legitimate packs: half plainly clean, half innocent look-alikes (refunds,
  payment agents, intra-group payers, disclosed directorships, split
  payments).

## 3. What counts as caught

A **fraudulent pack routed to review**, measured **per pack** on the rules'
route (`routing.path != "fast_track"`). The QA sample is ignored: a QA draw
that reaches an analyst is not detection.

The primary number is the rules' route, because that is what the system
applies. Two secondary numbers are reported but not claimed as the system's
behaviour today:

- the log-only gate's route with the AI team on, and
- how many runs carried a code-verified lead.

The AI team's gate is not applied; nothing it produces changes the route.

## 4. What counts as a false alarm

A **legitimate pack routed to review** (`routing.path != "fast_track"`),
measured per pack. A legitimate pack must be one whose quirk the application
actually disclosed where disclosure is the honest explanation (payment agent,
intra-group payer, disclosed directorship); otherwise the label is unfair.

## 5. The set runs once

One run, every result reported. After the run the set is used up and becomes
development data; a fresh set is needed for any new claim.

## 6. Independence

The people (or model) who decide what is in the set are not the people or AI
who built the checks. In order of credibility: the industry partner or sponsor
bank; a teammate who has not worked on the checks or prompts, working from a
public typology list; a different vendor's model with no access to this
repository, alongside a human author who reviews and edits it.

The renderer (`gldb/heldout.py`) draws the documents mechanically from the
sheet. It decides nothing: it maps a typology name to a generator fault.

## 7. Sealing the labels

The author keeps the answer key. Its SHA-256 is committed before the run
(`seal.json`); the key is revealed only to score. Changing a label afterwards
changes the hash. `python -m gldb.heldout score` verifies the seal before it
scores.

## 8. Published output

`python -m gldb.heldout score` writes a report (the controls page shows it) with
every pack, misses and false alarms included, recall and false-alarm rate with
95% intervals, and the seal.

## 9. What is not claimed

A full score on this set is a detection rate on unseen, synthetic packs built
from real typologies. It is not a rate on real applicants, whose documents
arrive as scans, screenshots and photos, and whose fraud differs in
distribution from what any synthetic set can represent.

Nor is it a rate on fraud nobody has foreseen. Nine of the eleven typologies
the renderer draws have a rule written for them, so the rules' recall shows
they catch what they were built for. What the set does measure fairly is the
false-alarm rate on honest look-alikes the system was not tuned on, and
whether the AI team finds the two typologies no rule looks for.
